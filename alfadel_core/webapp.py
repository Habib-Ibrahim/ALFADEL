from __future__ import annotations
from pathlib import Path
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
import json,base64,tempfile,webbrowser,threading,os,traceback
from .engine import NativeAnalyzer
from .legacy import Stage13Compatibility
from .workspace import WorkspaceStore
from .context_eval import evaluate_workspace_context
from .textio import decode_document_bytes
from .excel import build_alfadel_workbook

HERE=Path(__file__).resolve().parents[1]; STATIC=Path(__file__).resolve().parent/'static'; STAGE13=HERE/'vendor'/'stage13'
ENGINE=None
WORKSPACE=None

def _engine():
    global ENGINE
    if ENGINE is None:ENGINE=NativeAnalyzer(STAGE13)
    return ENGINE

def _workspace():
    global WORKSPACE
    if WORKSPACE is None: WORKSPACE=WorkspaceStore()
    return WORKSPACE

class H(BaseHTTPRequestHandler):
    def log_message(self,fmt,*args):pass
    def send(self,status,data,ctype='application/json; charset=utf-8'):
        b=data if isinstance(data,bytes) else data.encode('utf-8');self.send_response(status);self.send_header('Content-Type',ctype);self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
    def do_GET(self):
        if self.path in {'/','/index.html'}:return self.send(200,(STATIC/'index.html').read_text(encoding='utf-8'),'text/html; charset=utf-8')
        if self.path=='/api/status':
            e=_engine();w=_workspace();return self.send(200,json.dumps({'version':'1.0.1','native_mode':'stable-v1/alpha12-runtime+follow-active-inspector+xlsx-export+location-markers+txt-docx-import+workspace+portable-scholar-layers','historical':True,'ai_provider':e.ai.name,'ai_available':e.ai.available(),'training_tokens':e.training.counts,'lexicon_entries':len(e.lexicon.entries),'user_lexicon_entries':len(e.user_lexicon),'user_lexicon_path':str(e.user_lexicon.path),'correction_memory_entries':len(e.correction_memory),'correction_memory_path':str(e.correction_memory.path),'workspace_documents':len(w),'workspace_path':str(w.path),'legacy_orthography_rows':len(e.legacy_orthography)},ensure_ascii=False))
        if self.path=='/api/workspace':
            return self.send(200,json.dumps(_workspace().to_dict(),ensure_ascii=False))
        if self.path=='/api/user-lexicon':
            return self.send(200,json.dumps(_engine().user_lexicon.to_dict(),ensure_ascii=False))
        if self.path=='/api/correction-memory':
            return self.send(200,json.dumps(_engine().correction_memory.to_dict(),ensure_ascii=False))
        self.send(404,'not found','text/plain')
    def do_POST(self):
        try:
            n=int(self.headers.get('Content-Length','0'));req=json.loads(self.rfile.read(n) or b'{}')
            if self.path in {'/api/import-document','/api/import-text'}:
                raw=base64.b64decode(req.get('base64',''),validate=True);name=Path(req.get('filename','input.txt')).name
                decoded=decode_document_bytes(raw,name)
                return self.send(200,json.dumps({'text':decoded.text,'encoding':decoded.encoding,'kind':decoded.kind,'filename':name,'characters':len(decoded.text)},ensure_ascii=False))
            if self.path=='/api/analyze':
                rows=_engine().analyze(req.get('text',''),bool(req.get('historical',True)),bool(req.get('ai',False)),bool(req.get('memory',True)))
                return self.send(200,json.dumps({'tokens':[x.to_dict() for x in rows],'ai_available':_engine().ai.available()},ensure_ascii=False))
            if self.path=='/api/export-excel':
                tokens=req.get('tokens') or []
                if not isinstance(tokens,list): return self.send(400,json.dumps({'error':'tokens must be a list'}))
                if len(tokens)>1_048_575: return self.send(400,json.dumps({'error':'Excel .xlsx supports at most 1,048,575 data rows plus the header.'}))
                raw=build_alfadel_workbook(tokens)
                return self.send(200,raw,'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
            if self.path=='/api/user-lexicon/add':
                e=_engine().add_user_lexicon_entry(surface=req.get('surface',''),lemma=req.get('lemma',''),pos=req.get('pos',''),root=req.get('root',''),broad_pos=req.get('broad_pos',''),historical_matching=bool(req.get('historical_matching',True)),note=req.get('note',''))
                return self.send(200,json.dumps({'ok':True,'entry':e.__dict__,'count':len(_engine().user_lexicon)},ensure_ascii=False))
            if self.path=='/api/user-lexicon/remove':
                changed=_engine().remove_user_lexicon_entry(surface=req.get('surface',''),lemma=req.get('lemma',''),pos=req.get('pos',''),root=req.get('root',''))
                return self.send(200,json.dumps({'ok':changed,'count':len(_engine().user_lexicon)},ensure_ascii=False))
            if self.path=='/api/user-lexicon/update':
                e=_engine().update_user_lexicon_entry(req.get('old') or {},req.get('new') or {})
                return self.send(200,json.dumps({'ok':True,'entry':e.__dict__,'count':len(_engine().user_lexicon)},ensure_ascii=False))
            if self.path=='/api/user-lexicon/import':
                n=_engine().import_user_lexicon(req.get('payload') or {},req.get('mode','merge'))
                return self.send(200,json.dumps({'ok':True,'imported':n,'count':len(_engine().user_lexicon)},ensure_ascii=False))
            if self.path=='/api/correction-memory/commit':
                n=_engine().commit_corrections(req.get('corrections') or [])
                return self.send(200,json.dumps({'ok':True,'added':n,'count':len(_engine().correction_memory)},ensure_ascii=False))
            if self.path=='/api/correction-memory/import':
                n=_engine().import_correction_memory(req.get('payload') or {},req.get('mode','merge'))
                return self.send(200,json.dumps({'ok':True,'imported':n,'count':len(_engine().correction_memory)},ensure_ascii=False))
            if self.path=='/api/correction-memory/remove':
                changed=_engine().correction_memory.remove_event(req.get('event') or {})
                return self.send(200,json.dumps({'ok':changed,'count':len(_engine().correction_memory)},ensure_ascii=False))
            if self.path=='/api/correction-memory/clear':
                _engine().correction_memory.clear()
                return self.send(200,json.dumps({'ok':True,'count':0},ensure_ascii=False))
            if self.path=='/api/workspace/save':
                d=_workspace().upsert(req.get('project') or {},req.get('name',''),req.get('id',''))
                return self.send(200,json.dumps({'ok':True,'document':d,'workspace':_workspace().to_dict()},ensure_ascii=False))
            if self.path=='/api/workspace/remove':
                ok=_workspace().remove(req.get('id',''))
                return self.send(200,json.dumps({'ok':ok,'workspace':_workspace().to_dict()},ensure_ascii=False))
            if self.path=='/api/workspace/rename':
                ok=_workspace().rename(req.get('id',''),req.get('name',''))
                return self.send(200,json.dumps({'ok':ok,'workspace':_workspace().to_dict()},ensure_ascii=False))
            if self.path=='/api/workspace/import':
                n=_workspace().import_payload(req.get('payload') or {},req.get('mode','merge'))
                return self.send(200,json.dumps({'ok':True,'imported':n,'workspace':_workspace().to_dict()},ensure_ascii=False))
            if self.path=='/api/workspace/clear':
                _workspace().clear()
                return self.send(200,json.dumps({'ok':True,'workspace':_workspace().to_dict()},ensure_ascii=False))
            if self.path=='/api/workspace/evaluate-context':
                use_ai=bool(req.get('ai',False))
                ev=evaluate_workspace_context(_workspace().documents,_engine().ai,use_ai=use_ai,max_ai_tokens=int(req.get('max_ai_tokens',200)))
                return self.send(200,json.dumps(ev,ensure_ascii=False))
            if self.path=='/api/ai-suggest':
                eng=_engine()
                if not eng.ai.available(): return self.send(400,json.dumps({'error':'AI is not configured.'}))
                toks=req.get('tokens') or []; idx=int(req.get('index',-1))
                if idx<0 or idx>=len(toks): return self.send(400,json.dumps({'error':'Invalid token index.'}))
                res=eng.ai.suggest_unknown([str(x) for x in toks],idx)
                return self.send(200,json.dumps({'provider':eng.ai.name,**res},ensure_ascii=False))
            if self.path=='/api/modernize-an2':
                raw=base64.b64decode(req['base64']);name=Path(req.get('filename','input.an2')).name
                with tempfile.TemporaryDirectory(prefix='alfadel_') as td:
                    td=Path(td);p=td/name;p.write_bytes(raw);eng=Stage13Compatibility(STAGE13);out,rows=eng.modernize(p,td/'out')
                    return self.send(200,json.dumps({'tokens':[x.to_dict() for x in rows],'an3_base64':base64.b64encode(out.read_bytes()).decode('ascii'),'output_name':out.name},ensure_ascii=False))
            self.send(404,json.dumps({'error':'unknown endpoint'}))
        except Exception as e:
            self.send(500,json.dumps({'error':str(e),'trace':traceback.format_exc()},ensure_ascii=False))

def run_server(port=8765,open_browser=True):
    srv=ThreadingHTTPServer(('127.0.0.1',port),H);url=f'http://127.0.0.1:{port}/'
    print(f'ALFADEL core v1.0.1 running at {url}')
    if open_browser:threading.Timer(.7,lambda:webbrowser.open(url)).start()
    try:srv.serve_forever()
    except KeyboardInterrupt:pass
    finally:srv.server_close()
