import test from 'node:test';
import assert from 'node:assert/strict';
import {preferredCalendarEvents,calendarDayGroups} from '../dashboard/calendar-events.mjs';

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
  assert.deepEqual(groups.map(g=>[g.status,g.events.length]),[['scheduled',1],['published',1]]);
  assert.equal(groups[1].events[0],published);
});
test('published-only and IR days do not inherit an earnings scheduled state',()=>{
  assert.deepEqual(calendarDayGroups([release({})],'earnings').map(g=>g.status),['published']);
  const ir=release({type:'ir',status:'scheduled'});
  assert.deepEqual(calendarDayGroups([ir,release({})],'ir')[0].events,[ir]);
});
