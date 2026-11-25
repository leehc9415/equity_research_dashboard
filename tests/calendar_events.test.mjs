import test from 'node:test';
import assert from 'node:assert/strict';
import {preferredCalendarEvents,calendarDayGroups,useReportedBasis,hasBasisDifference,financialHistory,consensusComparison} from '../dashboard/calendar-events.mjs';

const release=overrides=>({corp:'00100000',date:'2026-10-28',type:'earnings',status:'published',basis:'개별 등',receipt:'separate',financials:{period:'2026.3Q'},...overrides});

test('same-company same-day quarter prefers consolidated regardless of arrival order',()=>{
  const separate=release({}),consolidated=release({basis:'연결',receipt:'consolidated'});
  assert.deepEqual(preferredCalendarEvents([separate,consolidated]),[consolidated]);
  assert.deepEqual(preferredCalendarEvents([consolidated,separate]),[consolidated]);
});
test('standalone-only company remains visible',()=>{
  const separate=release({basis:'별도'});assert.deepEqual(preferredCalendarEvents([separate]),[separate]);
});
test('different companies with identical display names stay distinct',()=>{
  const separate=release({company:'같은이름'}),other=release({corp:'00200000',company:'같은이름',basis:'연결'});
  assert.equal(preferredCalendarEvents([separate,other]).length,2);
});
test('different release dates or periods do not collapse',()=>{
  const consolidated=release({basis:'연결'}),otherDate=release({date:'2026-10-29'}),otherQuarter=release({financials:{period:'2026.2Q'}});
  assert.equal(preferredCalendarEvents([consolidated,otherDate,otherQuarter]).length,3);
});
test('IR and scheduled notices are preserved alongside consolidated earnings',()=>{
  const events=[release({basis:'연결'}),release({status:'scheduled'}),release({type:'ir'})];
  assert.deepEqual(preferredCalendarEvents(events),events);
});
test('missing period never causes a filing to be hidden',()=>{
  const events=[release({basis:'연결'}),release({financials:{}})];assert.equal(preferredCalendarEvents(events).length,2);
});
test('view selection never removes source data or changes financial figures',()=>{
  const events=[release({financials:{period:'2026.3Q',metrics:{operatingProfit:{value:8917}}}}),release({basis:'연결',financials:{period:'2026.3Q',metrics:{operatingProfit:{value:8992}}}})];
  const before=structuredClone(events);const selected=preferredCalendarEvents(events);
  assert.deepEqual(events,before);assert.equal(selected[0].financials.metrics.operatingProfit.value,8992);
});
test('mixed days keep scheduled and published records in separate groups',()=>{
  const scheduled=release({status:'scheduled'}),published=release({});
  const groups=calendarDayGroups([published,scheduled],'earnings');
  assert.deepEqual(groups.map(g=>[g.status,g.events.length]),[['published',1],['scheduled',1]]);
  assert.equal(groups[0].events[0],published);
});
test('published-only and IR days do not inherit an earnings scheduled state',()=>{
  assert.deepEqual(calendarDayGroups([release({})],'earnings').map(g=>g.status),['published']);
  const ir=release({type:'ir',status:'scheduled'});
  assert.deepEqual(calendarDayGroups([ir,release({})],'ir')[0].events,[ir]);
});
test('reported actuals are used for all companies, periods and accounting bases',()=>{
  for(const event of [release({corp:'00126362'}),release({corp:'00155276'}),release({financials:{period:'2026.4Q'}}),release({basis:'별도'}),release({basis:'연결'})])assert.equal(useReportedBasis(event),true);
});
test('scheduled and IR events never acquire an actual-result comparison',()=>{
  assert.equal(useReportedBasis(release({status:'scheduled'})),false);
  assert.equal(useReportedBasis(release({type:'ir'})),false);
});
test('a source mismatch keeps reported history and preserves actual zero values',()=>{
  const event=release({financials:{consensus:{comparable:{operatingProfit:false}},history:[{period:'2026.3Q',source:'DART',operatingProfit:0},{period:'2026.2Q',source:'DART',operatingProfit:120},{period:'2026.1Q',source:'Quantiwise',operatingProfit:null}]}});
  const before=structuredClone(event);
  assert.equal(hasBasisDifference(event),true);
  assert.deepEqual(financialHistory(event).map(row=>row.operatingProfit),[0,120]);
  assert.deepEqual(event,before);
});
test('consistent reference history remains available',()=>{
  const event=release({financials:{consensus:{comparable:{revenue:true,operatingProfit:true,netIncome:true}},history:[{source:'DART',operatingProfit:120},{source:'Quantiwise',operatingProfit:100}]}});
  assert.equal(hasBasisDifference(event),false);assert.equal(financialHistory(event).length,2);
});
test('a discrepancy in any metric uses reported history without inventing consensus',()=>{
  const event=release({financials:{consensus:{comparable:{revenue:true,operatingProfit:true,netIncome:false}},history:[{source:'DART',operatingProfit:120},{source:'Quantiwise',operatingProfit:100}]}});
  assert.equal(hasBasisDifference(event),true);assert.equal(financialHistory(event).length,1);
  delete event.financials.consensus;assert.equal(hasBasisDifference(event),false);assert.equal(event.financials.consensus,undefined);
});
test('negative estimates show positive percentages for narrowing losses',()=>{
  assert.equal(consensusComparison(-80,-100).text,'+20.0%');
  assert.equal(consensusComparison(-80,-100).positive,true);
});
test('negative estimates show negative percentages for widening losses',()=>{
  assert.equal(consensusComparison(-120,-100).text,'-20.0%');
  assert.equal(consensusComparison(-120,-100).positive,false);
});
test('loss-to-profit turns retain context while showing a percentage',()=>{
  const comparison=consensusComparison(20,-100);
  assert.equal(comparison.text,'+120.0%');assert.equal(comparison.description,'예상 적자→흑자');
});
test('positive estimates keep their usual percentage calculation',()=>{
  assert.equal(consensusComparison(120,100).text,'+20.0%');
  assert.equal(consensusComparison(-80,100).text,'-180.0%');
});
test('zero and missing estimates do not generate invalid percentages',()=>{
  assert.equal(consensusComparison(20,0).percent,null);
  assert.equal(consensusComparison(20,0).text,'예상 0→흑자');
  assert.equal(consensusComparison(20,null),null);assert.equal(consensusComparison(null,100),null);
});

test('monthly earnings are excluded from display while quarter and IR remain',()=>{
 const month=release({financials:{period:'2026.09~09'}}),quarter=release({receipt:'quarter'}),ir=release({type:'ir',status:'scheduled',period:'2026.09~09',financials:undefined});
 assert.deepEqual(preferredCalendarEvents([month,quarter,ir]),[quarter,ir]);
});
test('cumulative periods and unclassified notices remain visible',()=>{
 const cumulative=release({financials:{period:'2026.01~09'}}),unknown=release({financials:{}});
 assert.equal(preferredCalendarEvents([cumulative,unknown]).length,2);
});

import {reportRows,growth} from '../dashboard/calendar-events.mjs';
test('growth labels sign flips instead of percentages',()=>{
  assert.equal(growth(110,100).rate,10);
  assert.equal(growth(5,-10).label,'흑자전환');
  assert.equal(growth(-5,10).label,'적자전환');
  assert.equal(growth(-5,-10).label,'적자지속');
  assert.equal(growth(5,0).rate,null);
  assert.equal(growth(null,10).rate,null);
});
test('reportRows joins consensus, growth and stockeasy sector',()=>{
  const [a,b]=reportRows({companies:[
    {stock:'005930',company:'A',basis:'연결',receipt:'20261114000123',filed:'2026-11-14',marketCap:10,
     metrics:{operatingProfit:{value:110,priorYear:100,previous:-10}},consensus:{operatingProfit:80}},
    {stock:'000660',company:'B',basis:'별도',metrics:{},consensus:null},
  ]},{'005930':['반도체','메모리']});
  assert.deepEqual([a.sector,a.subSector],['반도체','메모리']);
  assert.equal(a.operatingProfit.vs.text,'+37.5%');
  assert.equal(a.operatingProfit.yoy,10);
  assert.equal(a.operatingProfit.qoqLabel,'흑자전환');
  assert.match(a.url,/rcpNo=20261114000123$/);
  assert.equal(b.operatingProfit.vs,null);
  assert.equal(b.sector,'미분류');
});
