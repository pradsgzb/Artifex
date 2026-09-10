from __future__ import annotations

import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

from qwen_archive.cli import build_parser
from qwen_archive.config import load_json_config
from qwen_archive.server_cli import build_parser as build_server_parser

ROOT=Path(__file__).resolve().parents[1]


def parameters(path: Path):
    text=path.read_text(encoding='utf-8-sig')
    block=text.split('param(',1)[1].split('\n)\n',1)[0]
    return set(re.findall(r'\$([A-Za-z][A-Za-z0-9]*)',block))


class ScriptTests(unittest.TestCase):
    def test_posix_scripts_pass_bash_syntax(self):
        for path in [*ROOT.glob('*.sh'),*ROOT.joinpath('bin').glob('*.sh')]:
            result=subprocess.run(['bash','-n',str(path)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,msg=f'{path}: {result.stderr}')

    def test_posix_launchers_are_executable(self):
        for name in ('prompts.sh','organize.sh','server.sh'):
            self.assertTrue(os.access(ROOT/'bin'/name,os.X_OK))

    def test_batch_launchers_are_separate(self):
        self.assertIn(' prompts ',(ROOT/'bin/prompts.sh').read_text())
        self.assertIn(' organize ',(ROOT/'bin/organize.sh').read_text())
        self.assertIn('qwen_archive.server_cli',(ROOT/'bin/server.sh').read_text())

    def test_powershell_batch_parameter_contracts_match(self):
        self.assertEqual(parameters(ROOT/'bin/prompts.ps1'),parameters(ROOT/'bin/organize.ps1'))

    def test_powershell_wrappers_expose_logging_switches(self):
        text=(ROOT/'bin/prompts.ps1').read_text()
        for value in ('LogLevel','LogPath','Color','NoColor'):
            self.assertIn(f'${value}',text)

    def test_powershell_wrappers_expose_adaptive_settings(self):
        text=(ROOT/'bin/common.ps1').read_text()
        for flag in ('--adaptive-generation','--max-new-tokens-ceiling','--minimum-image-side'):
            self.assertIn(flag,text)

    def test_server_has_dedicated_windows_launcher(self):
        text=(ROOT/'bin/server.ps1').read_text()
        self.assertIn('qwen_archive.server_cli',text)
        self.assertNotIn("-Task 'prompts'",text)

    def test_source_packager_excludes_runtime_and_models(self):
        text=(ROOT/'make-source-package.sh').read_text()
        self.assertIn("'runtime'",text); self.assertIn("'models'",text); self.assertIn("'node_modules'",text)


class CliTests(unittest.TestCase):
    def test_batch_parser_has_two_tasks(self):
        parser=build_parser(load_json_config())
        help_text=parser.format_help()
        self.assertIn('prompts',help_text); self.assertIn('organize',help_text)

    def test_batch_parser_accepts_color_and_adaptive_flags(self):
        parser=build_parser(load_json_config())
        args=parser.parse_args(['prompts','--color','--adaptive-generation','--max-new-tokens-ceiling','4096'])
        self.assertEqual(args.color_mode,'always'); self.assertTrue(args.adaptive_generation)

    def test_server_parser_accepts_independent_options(self):
        config=load_json_config(); parser=build_server_parser(config)
        args=parser.parse_args(['--port','9000','--no-color','--no-preload-model'])
        self.assertEqual(args.port,9000); self.assertEqual(args.color_mode,'never'); self.assertFalse(args.preload_model)

    def test_version_command(self):
        result=subprocess.run([sys.executable,str(ROOT/'qwen_archive.py'),'--version'],capture_output=True,text=True,cwd=ROOT)
        self.assertEqual(result.returncode,0); self.assertIn('2.0.0',result.stdout)


if __name__ == '__main__':
    unittest.main()
