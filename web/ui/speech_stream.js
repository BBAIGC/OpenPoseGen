import {request,isAbort} from './api.js';

export function pcmWav(chunks,sampleRate){
  const length=chunks.reduce((sum,chunk)=>sum+chunk.length,0),samples=new Float32Array(length);let offset=0;for(const chunk of chunks){samples.set(chunk,offset);offset+=chunk.length;}
  const rate=16000,count=Math.floor(length*rate/sampleRate),buffer=new ArrayBuffer(44+count*2),view=new DataView(buffer),text=(at,value)=>{for(let i=0;i<value.length;i++)view.setUint8(at+i,value.charCodeAt(i));};
  text(0,'RIFF');view.setUint32(4,36+count*2,true);text(8,'WAVE');text(12,'fmt ');view.setUint32(16,16,true);view.setUint16(20,1,true);view.setUint16(22,1,true);view.setUint32(24,rate,true);view.setUint32(28,rate*2,true);view.setUint16(32,2,true);view.setUint16(34,16,true);text(36,'data');view.setUint32(40,count*2,true);
  for(let i=0;i<count;i++){const position=i*sampleRate/rate,index=Math.floor(position),fraction=position-index,sample=Math.max(-1,Math.min(1,(samples[index]||0)*(1-fraction)+(samples[Math.min(index+1,length-1)]||0)*fraction));view.setInt16(44+i*2,sample<0?sample*32768:sample*32767,true);}return new Blob([buffer],{type:'audio/wav'});
}

export class SpeechSegmenter {
  constructor(rate,{silence=1.3,maxSeconds=20}={}){this.rate=rate;this.silence=silence;this.maxSeconds=maxSeconds;this.reset();}
  reset(){this.chunks=[];this.pre=[];this.preSamples=0;this.total=0;this.voiced=0;this.quiet=0;this.active=false;this.noise=.002;}
  take(){const result=this.voiced>=this.rate*.16?this.chunks:null;this.chunks=[];this.total=0;this.voiced=0;this.quiet=0;this.active=false;return result;}
  push(input){const data=new Float32Array(input);let power=0;for(const sample of data)power+=sample*sample;const rms=Math.sqrt(power/Math.max(1,data.length)),speaking=rms>=Math.max(.008,this.noise*3);if(!speaking)this.noise=Math.min(.006,this.noise*.97+rms*.03);
    if(!this.active){this.pre.push(data);this.preSamples+=data.length;while(this.preSamples>this.rate*.22&&this.pre.length>1)this.preSamples-=this.pre.shift().length;if(!speaking)return{rms,segment:null};this.active=true;this.chunks=this.pre;this.total=this.preSamples;this.pre=[];this.preSamples=0;this.voiced=data.length;}
    else{this.chunks.push(data);this.total+=data.length;if(speaking){this.voiced+=data.length;this.quiet=0;}else this.quiet+=data.length;}
    return{rms,segment:this.quiet>=this.rate*this.silence||this.total>=this.rate*this.maxSeconds?this.take():null};
  }
}

export class StreamingSpeech {
  constructor({button,live,cancel,stop,wave,status,createAnchor,onText,onError,onState}){
    Object.assign(this,{button,live,cancelButton:cancel,stopButton:stop,wave,status,createAnchor,onText,onError,onState});
    this.epoch=0;this.recording=false;this.starting=false;this.queue=[];this.recognizing=false;this.requestController=null;this.raf=0;this.level=0;this.anchor=null;this.history=Array(30).fill(0);
    this.bars=this.history.map(()=>{const bar=document.createElement('i');wave.append(bar);return bar;});
    button.onclick=()=>this.start();cancel.onclick=()=>this.cancel();stop.onclick=()=>this.finish();this.render();
  }
  async start(){
    if(this.recording||this.starting)return;
    if(!navigator.mediaDevices?.getUserMedia){this.onError?.('当前环境无法使用麦克风');return;}
    this.cancel();const epoch=this.epoch;this.starting=true;this.anchor=this.createAnchor?.();this.render('正在开启麦克风…');
    let stream,context;
    try{
      stream=await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true,noiseSuppression:true,autoGainControl:true}});
      if(epoch!==this.epoch){stream.getTracks().forEach(track=>track.stop());return;}
      const AudioContext=window.AudioContext||window.webkitAudioContext;context=new AudioContext({latencyHint:'interactive'});await context.resume();
      if(epoch!==this.epoch){stream.getTracks().forEach(track=>track.stop());await context.close();return;}
      this.stream=stream;this.context=context;this.source=context.createMediaStreamSource(stream);this.processor=context.createScriptProcessor(2048,1,1);this.gain=context.createGain();this.gain.gain.value=0;this.segmenter=new SpeechSegmenter(context.sampleRate);this.rate=context.sampleRate;this.recording=true;this.starting=false;
      this.processor.onaudioprocess=event=>{if(!this.recording||epoch!==this.epoch)return;const{rms,segment}=this.segmenter.push(event.inputBuffer.getChannelData(0));this.level=Math.min(1,rms*14);if(segment)this.enqueue(segment,epoch);};
      this.source.connect(this.processor);this.processor.connect(this.gain);this.gain.connect(context.destination);this.animate();this.render();
    }catch(error){stream?.getTracks().forEach(track=>track.stop());if(context&&context.state!=='closed')await context.close().catch(()=>{});if(epoch!==this.epoch)return;this.starting=false;this.recording=false;this.onError?.(error.name==='NotAllowedError'?'请开启麦克风权限':error.message);this.render();}
  }
  enqueue(chunks,epoch=this.epoch){
    if(epoch!==this.epoch||!chunks?.length)return;
    if(this.queue.length>=2){this.onError?.('识别较慢，已暂停录音，请等待当前语音识别完成');this.recording=false;this.stopAudio();this.render();return;}
    this.queue.push({chunks,rate:this.rate,epoch});this.render();this.pump();
  }
  async pump(){
    if(this.recognizing||!this.queue.length)return;const item=this.queue.shift();if(item.epoch!==this.epoch){this.pump();return;}
    this.recognizing=true;this.render();const controller=new AbortController();this.requestController=controller;
    try{const form=new FormData();form.append('audio',pcmWav(item.chunks,item.rate),'speech.wav');item.chunks.length=0;const data=await request('/speech',{method:'POST',body:form,signal:controller.signal});if(item.epoch===this.epoch&&data.text)this.onText?.(String(data.text),this.anchor);}
    catch(error){if(!isAbort(error)&&item.epoch===this.epoch)this.onError?.(error.message);}
    finally{item.chunks.length=0;if(this.requestController===controller)this.requestController=null;if(item.epoch===this.epoch){this.recognizing=false;this.render();this.pump();if(!this.recording&&!this.queue.length&&!this.recognizing){this.anchor?.dispose?.();this.anchor=null;}}}
  }
  finish(){if(this.starting){this.cancel();return;}if(this.recording){const chunks=this.segmenter?.take();this.recording=false;if(chunks)this.enqueue(chunks);this.stopAudio();}this.render();if(!this.recognizing&&!this.queue.length){this.anchor?.dispose?.();this.anchor=null;}}
  cancel(){this.epoch++;this.recording=false;this.starting=false;this.queue.length=0;this.recognizing=false;this.requestController?.abort();this.requestController=null;this.segmenter?.reset();this.stopAudio();this.anchor?.dispose?.();this.anchor=null;this.render();}
  stopAudio(){if(this.raf)cancelAnimationFrame(this.raf);this.raf=0;if(this.processor)this.processor.onaudioprocess=null;for(const node of[this.source,this.processor,this.gain]){try{node?.disconnect();}catch{}}this.source=this.processor=this.gain=null;this.stream?.getTracks().forEach(track=>track.stop());this.stream=null;const context=this.context;this.context=null;if(context&&context.state!=='closed')context.close().catch(()=>{});this.level=0;}
  animate(){if(!this.recording)return;this.history.shift();this.history.push(this.level);this.bars.forEach((bar,index)=>{bar.style.height=(2+this.history[index]*19)+'px';bar.style.opacity=String(.35+this.history[index]*.6);});this.raf=requestAnimationFrame(()=>this.animate());}
  render(message){const busy=this.starting||this.recording||this.recognizing||this.queue.length;this.live.hidden=!busy;this.button.hidden=!!busy;this.stopButton.disabled=!this.recording&&!this.starting;this.live.classList.toggle('recognizing',this.recognizing);this.status.hidden=!busy;this.status.textContent=message||(this.recognizing?(this.recording?'识别中 · 可继续说话':'正在完成识别…'):(this.recording?'静音后自动识别':'正在准备识别…'));this.onState?.({recording:this.recording,recognizing:this.recognizing,queued:this.queue.length});}
  stats(){return{recording:this.recording,recognizing:this.recognizing,queued:this.queue.length,audioContext:!!this.context,tracks:this.stream?.getTracks().filter(track=>track.readyState==='live').length||0,animation:!!this.raf};}
}
