"""Public release labels must agree with the CLI and actual MCP handshake."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from version import VERSION


class ReleaseVersionTests(unittest.TestCase):
    def test_public_labels_cli_and_management_protocol_agree(self):
        root = Path(__file__).parent
        self.assertIn('Version ' + VERSION, (root / 'README.md').read_text(encoding='utf-8'))
        self.assertIn('Public release ' + VERSION, (root / 'PUBLIC_REVIEW.md').read_text(encoding='utf-8'))
        cli = subprocess.run([sys.executable, str(root / 'main.py'), '--version'],
            text=True, capture_output=True, check=True, timeout=10)
        self.assertEqual(cli.stdout.strip(), VERSION)
        with tempfile.TemporaryDirectory(dir=root) as temp:
            base = Path(temp)
            result = subprocess.run([sys.executable, str(root / 'main.py'), '--mcp-management',
                '--data', str(base / 'data'), '--codex-home', str(base / 'home')],
                input=json.dumps({'id': 1, 'method': 'initialize'}) + '\n',
                text=True, capture_output=True, check=True, timeout=10)
            self.assertEqual(json.loads(result.stdout)['result']['serverInfo']['version'], VERSION)


if __name__ == '__main__':
    unittest.main()
