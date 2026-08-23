from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import tempfile,subprocess,sys
from .models import Candidate,TokenAnalysis,Evidence
from .taxonomy import tag_from_composite_pos

@dataclass(frozen=True)
class LegacyRow:
    surface:str;paragraph:str;word_index:str;root:str;lemma:str;secondary:str;pos:str
    @property
    def key(self):return (self.paragraph,self.word_index,self.surface)

def read_analyzed(path:Path):
    data=Path(path).read_bytes()
    try:text=data.decode('cp1256')
    except UnicodeDecodeError:text=data.decode('utf-8-sig')
    rows=[]
    for line in text.splitlines():
        if not line.strip():continue
        f=line.rstrip('\x00').split('\t'); f+=['']*(16-len(f))
        rows.append(LegacyRow(f[0],f[1],f[2],f[3],f[4],f[5],f[6]))
    return rows

def group(rows):
    from collections import OrderedDict
    d=OrderedDict()
    for r in rows:d.setdefault(r.key,[]).append(r)
    return list(d.items())

class Stage13Compatibility:
    def __init__(self,stage13_root:Path):self.root=Path(stage13_root)
    def modernize(self,an2:Path,output_dir:Path):
        output_dir=Path(output_dir);output_dir.mkdir(parents=True,exist_ok=True);pref=output_dir/Path(an2).stem
        runner=self.root/'tools'/'run_stage13_frozen.py'
        subprocess.run([sys.executable,str(runner),'--an2',str(an2),'--output-prefix',str(pref)],check=True)
        out=pref.with_suffix('.stage13_FROZEN.an3')
        rows=read_analyzed(out);ans=[]
        for i,(key,rs) in enumerate(group(rows),1):
            r=rs[0];c=Candidate(r.lemma,r.pos,r.root,r.secondary,tag_from_composite_pos(r.pos),0,
                                [Evidence('frozen_stage13','validated compatibility pipeline',0)])
            parts=(r.paragraph or '').split(':')
            page=parts[0] if len(parts)>0 and parts[0] not in {'','0'} else ''
            paragraph=parts[1] if len(parts)>1 and parts[1] not in {'','0'} else ''
            line=parts[2] if len(parts)>2 and parts[2] not in {'','0'} else ''
            ans.append(TokenAnalysis(i,r.surface,[c],0,'stage13-compatibility',
                                     page=page,paragraph=paragraph,line=line,
                                     word_in_paragraph=0,legacy_location=r.paragraph or '0:0:0'))
        return out,ans
