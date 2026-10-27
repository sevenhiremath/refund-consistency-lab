"""Verifyable deadline repair: exact recorded 600/300 still and direct voice mux.

Run after node render.mjs final. No speech timing or tempo transformation occurs.
The intermediate Remotion export is preserved for comparison.
"""
import subprocess,shutil
from pathlib import Path
def run(args):subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y',*args],check=True)
source=Path('out/hisaab-remotion-render.mp4')
if not source.exists():shutil.copy2('out/hisaab-final.mp4',source)
still=Path('public/media/duplicate-600-authentic.png')
if not still.exists():run(['-ss','10.5','-i','public/media/01-duplicates.mp4','-frames:v','1',str(still)])
run(['-i',str(source),'-loop','1','-i',str(still),'-i','public/media/voice-normalized.wav',
     '-filter_complex',"[1:v]crop=1250:525:400:200,scale=1744:727[proof];[0:v][proof]overlay=88:214:enable='between(t,48.15,52.149)'[v]",
     '-map','[v]','-map','2:a:0','-c:v','libx264','-preset','fast','-crf','20','-threads','6','-pix_fmt','yuv420p',
     '-c:a','aac','-b:a','192k','-ar','48000','-t','116','-movflags','+faststart','out/hisaab-final.mp4'])
print('Final evidence hold and direct narration mux exported.',flush=True)
