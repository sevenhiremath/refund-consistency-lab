import fs from 'node:fs';
const edit=JSON.parse(fs.readFileSync('edit.json','utf8'));
let cursor=0;let missing=[];
for(const s of edit.scenes){if(Math.abs(s.start-cursor)>0.002)throw Error(`Timeline gap/overlap at ${s.start}`);cursor+=s.duration;if(s.media&&!fs.existsSync('public/media/'+s.media))throw Error(`Missing file ${s.media}`);if(s.missing)missing.push(s.missing);}
if(Math.abs(cursor-edit.duration)>0.002)throw Error(`Duration ${cursor}`);
for(const c of edit.captions){if(c.end<=c.start||c.end>edit.duration)throw Error('Invalid caption timing');}
console.log('Timeline:',edit.duration,'seconds; referenced media present.');
console.log('Required inputs still missing:',missing.length,'scene(s), voice:',!edit.voice,'music:',!edit.music);
if(process.argv.includes('--final')&&(missing.length||!edit.voice||!edit.captions.length))throw Error('Final blocked: evidence, voice or voice-aligned captions missing');
const stamp=n=>{let m=Math.round(n*1000);let h=Math.floor(m/3600000);m%=3600000;let min=Math.floor(m/60000);m%=60000;return `${String(h).padStart(2,'0')}:${String(min).padStart(2,'0')}:${String(Math.floor(m/1000)).padStart(2,'0')},${String(m%1000).padStart(3,'0')}`;};
if(edit.captions.length)fs.writeFileSync('hisaab.srt',edit.captions.map((c,i)=>`${i+1}\n${stamp(c.start)} --> ${stamp(c.end)}\n${c.text}\n`).join('\n'));
