'use client';
import {useState} from 'react';

export default function MaritimeDataPanel({api,run,health}) {
 const [dataset,setDataset]=useState('real_annual');
 const [yearStart,setYearStart]=useState('2020'),[yearEnd,setYearEnd]=useState('2025');
 const [start,setStart]=useState('2025-01-01'),[end,setEnd]=useState('2025-01-31');
 const [vessel,setVessel]=useState(''),[voyage,setVoyage]=useState('');
 const [question,setQuestion]=useState('선택한 조건의 기록을 조회해줘'),[useModel,setUseModel]=useState(false);
 const [result,setResult]=useState(null),[agent,setAgent]=useState(null);
 function reset(){setResult(null);setAgent(null);}
 function change(setter,value){setter(value);reset();}
 function scope(offset=0){return {dataset,limit:20,offset,...(dataset==='real_annual'?{year_start:Number(yearStart),year_end:Number(yearEnd)}:{start,end,...(voyage.trim()?{voyage_id:voyage.trim()}: {})}),...(vessel.trim()?{vessel_id:vessel.trim()}:{})};}
 async function query(offset=0){reset();setResult(await api('/maritime-data/query',scope(offset)));}
 async function ask(){reset();const data=await api('/maritime-data/ask',{question,scope:scope(),use_model:useModel});setAgent(data);setResult(data.result);}
 const annual=result?.dataset==='real_annual';
 return <>
  <section className="panel"><h2>해사 데이터 조회</h2><p>실제 MRV와 개발용 합성 Noon을 구분해 조회합니다. 아래 조건만 적용되며 기존 운항 화면의 선박 선택과는 별개입니다.</p>
   <div className="row"><label>자료<select value={dataset} onChange={e=>{change(setDataset,e.target.value);setVessel('');setVoyage('');}}><option value="real_annual">실제 MRV · 보고기간 자료</option><option value="synthetic_noon">합성 Noon · 개발용 일별 자료</option></select></label>
   {dataset==='real_annual'?<><label>시작 연도<input type="number" min="1900" max="2100" value={yearStart} onChange={e=>change(setYearStart,e.target.value)}/></label><label>종료 연도<input type="number" min="1900" max="2100" value={yearEnd} onChange={e=>change(setYearEnd,e.target.value)}/></label></>:<><label>Noon 시작일<input type="date" value={start} onChange={e=>change(setStart,e.target.value)}/></label><label>Noon 종료일<input type="date" value={end} onChange={e=>change(setEnd,e.target.value)}/></label></>}
   </div><div className="row"><label>선박 ID (선택)<input value={vessel} placeholder={dataset==='real_annual'?'REAL:IMO:1234567':'SYN:SIM-BULK-01'} onChange={e=>change(setVessel,e.target.value)}/></label>{dataset==='synthetic_noon'&&<label>항차 ID (선택)<input value={voyage} placeholder="SYN:SIM-BULK-01-V001" onChange={e=>change(setVoyage,e.target.value)}/></label>}<button className="primary" onClick={()=>run(()=>query())}>기록 조회</button></div>
   <p className="muted">MRV는 최대 10년 차이, Noon은 최대 366일 차이로 조회합니다. ID를 비우면 선택 기간의 전체 선박을 조회합니다.</p>
  </section>
  <section className="panel"><h2>조회 도우미</h2><p>위 조건으로 원본 기록을 조회합니다. 계산·등급·합계·비교는 지원하지 않습니다. 질문 속 선박·기간은 자동 적용하지 않습니다.</p><label>조회 요청<textarea rows={2} maxLength={2000} value={question} onChange={e=>{setQuestion(e.target.value);reset();}}/></label><div className="row"><label className="check"><input type="checkbox" disabled={!health?.llm} checked={useModel} onChange={e=>{setUseModel(e.target.checked);reset();}}/>모델로 조회 도구 선택 {health?.llm?'':'(모델 미설정: 규칙 경로 사용)'}</label><button disabled={!question.trim()} onClick={()=>run(ask)}>조회 도우미 실행</button></div>{agent&&<><p role="status">{agent.answer}</p><p className="muted">처리 경로: {agent.routing} · {agent.actions.join(' → ')||'조회 미실행'}</p>{agent.warnings.map(w=><p className="warning" key={w}>{w==='LLM_ROUTING_FAILED_RULE_FALLBACK'?'모델 응답을 검증하지 못해 규칙 경로로 조회했습니다.':w==='LLM_NOT_CONFIGURED'?'모델이 설정되지 않아 규칙 경로로 조회했습니다.':'지원 범위를 확인하세요.'}</p>)}</>}
  </section>
  {result&&<section className="panel"><h2>{annual?'실제 MRV':'합성 Noon · 개발용'} 조회 결과</h2><p>전체 {result.total.toLocaleString()}건 · 현재 {result.rows.length}건 {result.rows.length>0&&`(${result.offset+1}~${result.offset+result.rows.length})`}</p>{result.warnings.map(w=><p className="notice" key={w}>{w}</p>)}
   <div className="table-wrap"><table><thead><tr><th>기간</th><th>선박 / 항차</th><th>연료 (t)</th><th>{annual?'보고 CO₂ (t)':'거리 (nm)'}</th><th>품질 / 출처</th></tr></thead><tbody>{result.rows.map(r=><tr key={r.record_id}><td>{annual?r.reporting_year:r.report_date}</td><td>{r.vessel_name}<small>{r.vessel_id}</small>{!annual&&<small>{r.voyage_id}</small>}</td><td>{r.fuel_t??'결측'}</td><td>{(annual?r.co2_t:r.distance_nm)??'결측'}</td><td>{r.quality_status}<details><summary>원본 근거</summary><p>{r.source_filename} · 행 {r.source_row}</p><pre>{JSON.stringify(r,null,2)}</pre></details></td></tr>)}</tbody></table></div>
   {!result.rows.length&&<p role="status">선택한 조건에 해당하는 기록이 없습니다.</p>}
   <div className="row"><button disabled={result.offset===0} onClick={()=>run(()=>query(Math.max(0,result.offset-result.limit)))}>이전 페이지</button><button disabled={!result.has_more} onClick={()=>run(()=>query(result.next_offset))}>다음 페이지</button></div>
  </section>}
 </>;
}
