"""Small stdio MCP server. Explicit per-server thread binding, no caller spoof claims."""
import json
import sys
import threading
from compaction import CompactionQueue
from core import thread_id


def serve(store, bound_thread):
    bound = thread_id(bound_thread)
    queue = CompactionQueue(store)
    stopped = threading.Event()
    queue.shutdown_event = stopped
    def worker():
        while not stopped.wait(5):
            try:
                queue.tick()
            except Exception:
                # Queue remains durable. No stdout diagnostics in MCP transport.
                pass
    worker_thread = threading.Thread(target=worker, daemon=False)
    worker_thread.start()
    tools = [
        {'name': 'request_compaction_after_turn', 'description': 'Queue compaction AFTER your completed response and live idle. Does NOT compact inside this tool call. KNOWN UNSAFE: concurrent new input may be rejected or its active answer interrupted. Real dispatch requires separate per-chat user risk consent. Explicit origin turn ID required; fixed server binding is not authenticated caller metadata.',
         'inputSchema': {'type': 'object', 'properties': {'origin_turn_id': {'type': 'string'}}, 'required': ['origin_turn_id'], 'additionalProperties': False}},
        {'name': 'get_compaction_request', 'description': 'Read queued/deferred/completed/ambiguous status; ACK is not completion.',
         'inputSchema': {'type': 'object', 'properties': {'request_id': {'type': 'string'}}, 'required': ['request_id'], 'additionalProperties': False}},
        {'name': 'cancel_compaction_request', 'description': 'Cancel this chat request before dispatch; cannot cancel ambiguous already-sent work.',
         'inputSchema': {'type': 'object', 'properties': {'request_id': {'type': 'string'}}, 'required': ['request_id'], 'additionalProperties': False}}]
    sys.stdout.reconfigure(encoding='utf-8')
    try:
        for line in sys.stdin.buffer:
            rid = None
            try:
                request = json.loads(line)
                rid = request.get('id')
                method = request.get('method')
                if rid is None:
                    continue
                if method == 'initialize':
                    result = {'protocolVersion': '2024-11-05', 'capabilities': {'tools': {}},
                        'serverInfo': {'name': 'codex-context-keeper', 'version': '0.2.1'},
                        'instructions': 'Bound to thread ' + bound + '. Identity is explicit, not authenticated by stdio metadata. KNOWN UNSAFE native compaction only after completed origin, live idle and separate per-chat user risk consent. Concurrent input can be rejected or its answer interrupted.'}
                elif method == 'ping':
                    result = {}
                elif method == 'tools/list':
                    result = {'tools': tools}
                elif method == 'tools/call':
                    params = request.get('params', {})
                    args = params.get('arguments', {})
                    try:
                        if params['name'] == 'request_compaction_after_turn':
                            value = queue.request(bound, args['origin_turn_id'], bound)
                        elif params['name'] == 'get_compaction_request':
                            value = queue.status(args['request_id'], bound)
                        elif params['name'] == 'cancel_compaction_request':
                            value = queue.cancel(args['request_id'], bound)
                        else:
                            raise ValueError('Неизвестный инструмент.')
                        result = {'content': [{'type': 'text', 'text': json.dumps(value, ensure_ascii=False)}], 'isError': False}
                    except Exception as exc:
                        result = {'content': [{'type': 'text', 'text': str(exc)}], 'isError': True}
                else:
                    print(json.dumps({'jsonrpc': '2.0', 'id': rid, 'error': {'code': -32601, 'message': 'Method not found'}}), flush=True)
                    continue
                print(json.dumps({'jsonrpc': '2.0', 'id': rid, 'result': result}, ensure_ascii=False), flush=True)
            except Exception:
                print(json.dumps({'jsonrpc': '2.0', 'id': rid, 'error': {'code': -32600, 'message': 'Invalid request'}}), flush=True)
    finally:
        stopped.set()
        # Graceful EOF must drain the current bounded native/archive operation.
        # A daemon exit here could strand core.lock despite a normal process exit.
        worker_thread.join()
