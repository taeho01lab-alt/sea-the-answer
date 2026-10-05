import {spawn} from 'node:child_process';
import {existsSync} from 'node:fs';
import {resolve,dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
const root=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const python=resolve(root,process.platform==='win32'?'.venv/Scripts/python.exe':'.venv/bin/python');
if(!existsSync(python)||!existsSync(resolve(root,'.env.local'))||!existsSync(resolve(root,'web/node_modules/next/dist/bin/next'))){
 console.error('Run setup-local.ps1 or follow README setup first.');process.exit(1);
}
let stopping=false;
const children=[];
function stop(){if(stopping)return;stopping=true;for(const child of children)child.kill();}
for(const [cmd,args,cwd] of [[python,['-m','service.app'],root],[process.execPath,['node_modules/next/dist/bin/next','dev','--hostname','127.0.0.1','--port','3000'],resolve(root,'web')]]){
 const child=spawn(cmd,args,{cwd,stdio:'inherit',windowsHide:true,env:{...process.env,PYTHONUTF8:'1',NEXT_TELEMETRY_DISABLED:'1'}});children.push(child);
 child.on('error',e=>{console.error(e.message);process.exitCode=1;stop();});
 child.on('exit',code=>{if(!stopping){process.exitCode=code||0;stop();}});
}
for(const signal of ['SIGINT','SIGTERM'])process.on(signal,stop);
console.log('HAEDAP design stack: http://127.0.0.1:3000 / API: http://127.0.0.1:8000');
