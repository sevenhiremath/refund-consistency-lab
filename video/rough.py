"""Deadline fallback: real media plus the existing rendered Remotion animation.

Run from video/. It preserves the original voice speed and mutes source captures.
"""
import json, subprocess
from pathlib import Path

edit=json.loads(Path('edit.json').read_text(encoding='utf8'))
out=Path('out/rough-parts');out.mkdir(parents=True,exist_ok=True)
def run(args):
    subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y',*args],check=True)
files=[]
for i,s in enumerate(edit['scenes']):
    target=out/f'{i:02d}.mp4';files.append(target)
    dur=s['duration'];media=s.get('media');kind=s['type']
    if media:
        src=Path('public/media')/media
        trim=s.get('trimStart',0);play=s.get('playDuration',dur)
        args=['-ss',str(trim),'-t',str(play),'-i',str(src)]
        if media.startswith(('01','02','03')):
            vf='crop=1250:525:400:200,scale=1152:484'
        elif media.startswith('05'):
            vf='crop=1720:520:100:40,scale=1152:348,pad=1152:484:0:0:color=0x101416'
        elif media.startswith('06'):
            vf='crop=1420:800:400:50,scale=1152:649, crop=1152:484:0:0'
        else:
            vf='crop=1250:750:400:0,scale=1152:691,crop=1152:484:0:0'
        title=out/f'{i:02d}-title.txt';title.write_text(s['title'],encoding='utf8')
        note=out/f'{i:02d}-note.txt';note.write_text(s.get('note',''),encoding='utf8')
        vf+=f",pad=1280:720:64:140:color=0xF4F1EC,drawtext=fontfile='C\\:/Windows/Fonts/segoeui.ttf':textfile='{title.as_posix()}':x=64:y=77:fontsize=28:fontcolor=0x17191B"
        vf+=f",drawtext=fontfile='C\\:/Windows/Fonts/segoeui.ttf':text='HISAAB':x=64:y=30:fontsize=18:fontcolor=0x249D7E"
        if s.get('label'):
            lab=out/f'{i:02d}-label.txt';lab.write_text(s['label'],encoding='utf8')
            vf+=f",drawtext=fontfile='C\\:/Windows/Fonts/segoeui.ttf':textfile='{lab.as_posix()}':x=64:y=116:fontsize=16:fontcolor=0x249D7E"
    else:
        source={'problem':0,'architecture':28,'close':112}.get(kind,101 if 'TEST SOURCE' in s.get('label','') else 108)
        length=min(dur,20 if kind=='problem' else 5 if kind=='close' else 8)
        args=['-ss',str(source),'-t',str(length),'-i','out/hisaab-backup-assembly.mp4']
        vf='scale=1280:720'
    vf+=f',fps=30,tpad=stop_mode=clone:stop_duration={dur},setsar=1'
    run([*args,'-an','-vf',vf,'-t',str(dur),'-c:v','libx264','-preset','ultrafast','-crf','24','-pix_fmt','yuv420p',str(target)])
    print('Rough scene',i+1,'/',len(edit['scenes']),flush=True)
listing=out/'concat.txt';listing.write_text('\n'.join("file '"+p.name+"'" for p in files))
run(['-f','concat','-safe','0','-i',str(listing),'-i','public/media/'+edit['voice'],'-map','0:v:0','-map','1:a:0','-c:v','copy','-c:a','aac','-b:a','192k','-t',str(edit['duration']),'-movflags','+faststart','out/hisaab-complete-rough.mp4'])
print('Complete rough exported.',flush=True)
