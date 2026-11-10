"""Synthetic Linux IPC fixture. Never contacts a real Codex owner."""
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
import uuid


@unittest.skipUnless(sys.platform == 'linux', 'Requires a real Linux Unix socket and ELF bridge')
class LinuxIpcTests(unittest.TestCase):
    def test_counter_uses_authenticated_unix_stream_without_starting_a_turn(self):
        bridge = Path(__file__).parent / 'dist/ContextKeeperBridge'
        if not bridge.is_file():
            self.skipTest('Build/extract the Linux bridge to dist before native acceptance')
        with tempfile.TemporaryDirectory(prefix='ck-ipc-') as temp:
            root = Path(temp)
            home = root / 'home'
            home.mkdir()
            path = root / 'fixture.sock'
            tid = str(uuid.uuid4())
            errors, methods = [], []
            def read(conn):
                def exact(count):
                    value = b''
                    while len(value) < count:
                        part = conn.recv(count - len(value))
                        if not part:
                            raise EOFError('Fixture stream ended')
                        value += part
                    return value
                count = struct.unpack('<I', exact(4))[0]
                return json.loads(exact(count))
            def send(conn, value):
                raw = json.dumps(value).encode()
                conn.sendall(struct.pack('<I', len(raw)) + raw)
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            listener.bind(str(path))
            listener.listen(1)
            listener.settimeout(5)
            def serve():
                try:
                    with listener.accept()[0] as conn:
                        conn.settimeout(5)
                        for method in ('initialize', 'thread-owner-discovery'):
                            request = read(conn)
                            methods.append(request['method'])
                            if request['method'] != method:
                                raise AssertionError('Unexpected native operation')
                            send(conn, {'type': 'response', 'requestId': request['requestId'], 'resultType': 'success',
                                'handledByClientId': 'fixture-owner', 'result': {'clientId': 'fixture-client'}})
                        follow = read(conn)
                        methods.append(follow['method'])
                        state = {'id': tid, 'threadRuntimeStatus': {'type': 'idle'}, 'resumeState': 'resumed',
                            'latestTokenUsageInfo': {'last': {'totalTokens': 125}, 'total': {'totalTokens': 99999},
                                'modelContextWindow': 1000}, 'turns': []}
                        send(conn, {'type': 'broadcast', 'method': 'thread-stream-state-changed', 'version': 11,
                            'sourceClientId': 'fixture-owner', 'params': {'conversationId': tid, 'hostId': 'local',
                                'change': {'type': 'snapshot', 'revision': 1, 'conversationState': state}}})
                        methods.append(read(conn)['method'])  # Release the observer lease, not a user turn.
                except Exception as exc:
                    errors.append(repr(exc))
            thread = threading.Thread(target=serve, daemon=True)
            thread.start()
            try:
                result = subprocess.run([str(bridge), 'observe', tid, '-'],
                    env=dict(os.environ, CODEX_HOME=str(home), CONTEXT_KEEPER_IPC_SOCKET=str(path)),
                    capture_output=True, text=True, encoding='utf-8', timeout=10)
                thread.join(6)
                self.assertFalse(thread.is_alive())
                self.assertEqual(errors, [])
                self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                value = json.loads(result.stdout)
                self.assertEqual(value['disposition'], 'observed')
                self.assertEqual((value['usedTokens'], value['contextWindow'], value['remainingTokens']), (125, 1000, 875))
                self.assertEqual(methods, ['initialize', 'thread-owner-discovery',
                    'thread-stream-following-changed', 'thread-stream-following-changed'])
            finally:
                listener.close()
                thread.join(6)


if __name__ == '__main__':
    unittest.main()
