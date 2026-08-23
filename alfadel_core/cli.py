from __future__ import annotations
from pathlib import Path
import argparse,json
from .engine import NativeAnalyzer
from .legacy import Stage13Compatibility
from .textio import read_document_file

HERE=Path(__file__).resolve().parents[1]
STAGE13=HERE/'vendor'/'stage13'

def main(argv=None):
    ap=argparse.ArgumentParser(prog='alfadel',description='ALFADEL core v1.0.1')
    sub=ap.add_subparsers(dest='cmd',required=True)
    n=sub.add_parser('analyze');n.add_argument('text_or_file');n.add_argument('--literal',action='store_true');n.add_argument('--no-historical',action='store_true');n.add_argument('--ai',action='store_true');n.add_argument('--json')
    m=sub.add_parser('modernize-an2');m.add_argument('an2');m.add_argument('--output-dir',default='alfadel_output')
    s=sub.add_parser('serve');s.add_argument('--port',type=int,default=8765);s.add_argument('--no-browser',action='store_true')
    a=ap.parse_args(argv)
    if a.cmd=='serve':
        from .webapp import run_server;return run_server(a.port,not a.no_browser)
    if a.cmd=='analyze':
        txt=a.text_or_file if a.literal else read_document_file(a.text_or_file).text
        eng=NativeAnalyzer(STAGE13);rows=eng.analyze(txt,not a.no_historical,a.ai);data=[x.to_dict() for x in rows]
        js=json.dumps(data,ensure_ascii=False,indent=2)
        if a.json:Path(a.json).write_text(js,encoding='utf-8')
        else:print(js)
    elif a.cmd=='modernize-an2':
        eng=Stage13Compatibility(STAGE13);out,rows=eng.modernize(Path(a.an2),Path(a.output_dir));print(out)
