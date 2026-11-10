"""Small stdio MCP server. Explicit per-server thread binding, no caller spoof claims."""
import json
import sys
import threading
from compaction import CompactionQueue
from core import thread_id
from version import VERSION


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
        {'name': 'request_compaction_after_turn', 'description': 'Queue a request now; execution waits for a completed originating turn and live idle. Does not compact inside this tool call. Requires per-chat request and external-launch permissions, both off by default. Explicit origin turn ID required; server binding is explicit, not authenticated caller metadata.',
         'inputSchema': {'type': 'object', 'properties': {'origin_turn_id': {'type': 'string'}}, 'required': ['origin_turn_id'], 'additionalProperties': False}},
        {'name': 'get_compaction_request', 'description': 'Read queued/deferred/completed/ambiguous status; ACK is not completion.',
         'inputSchema': {'type': 'object', 'properties': {'request_id': {'type': 'string'}}, 'required': ['request_id'], 'additionalProperties': False}},
        {'name': 'cancel_compaction_request', 'description': 'Cancel this chat request before dispatch; cannot cancel ambiguous already-sent work.',
         'inputSchema': {'type': 'object', 'properties': {'request_id': {'type': 'string'}}, 'required': ['request_id'], 'additionalProperties': False}}]
    def call(name, args):
        if name == 'request_compaction_after_turn':
            return queue.request(bound, args['origin_turn_id'], bound)
        if name == 'get_compaction_request':
            return queue.status(args['request_id'], bound)
        if name == 'cancel_compaction_request':
            return queue.cancel(args['request_id'], bound)
        raise ValueError('Неизвестный инструмент.')
    try:
        serve_stdio(tools, 'Bound to thread ' + bound + '. Identity is explicit, not authenticated by stdio metadata. '
            'Compaction requires both per-chat permissions, a completed originating turn and live idle. Both permissions default off.', call)
    finally:
        stopped.set()
        # Graceful EOF drains current work instead of stranding an archive lock.
        worker_thread.join()


def serve_stdio(tools, instructions, call, *, name='codex-context-keeper'):
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
                        'serverInfo': {'name': name, 'version': VERSION},
                        'instructions': instructions}
                elif method == 'ping':
                    result = {}
                elif method == 'tools/list':
                    result = {'tools': tools}
                elif method == 'tools/call':
                    params = request.get('params', {})
                    args = params.get('arguments', {})
                    try:
                        value = call(params['name'], args)
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
        sys.stdout.flush()
