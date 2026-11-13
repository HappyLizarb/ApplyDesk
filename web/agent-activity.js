// One master conversation can include many sequential model requests and tool steps.
export function groupAgentRuns(runs) {
  const groups=[],masters=new Map();
  for(const run of runs) {
    const previous=groups.at(-1);
    const key=run.role==='master'?(run.turn_id||(!previous?.turn_id&&previous?.role==='master'?previous.id:run.id)):null;
    const current=key&&masters.get(key);
    if(current) {
      current.steps++;
      current.status=run.status;
      current.model=run.model;
      current.finished_at=run.finished_at;
    } else {
      const group={...run,steps:1};
      groups.push(group);
      if(key)masters.set(key,group);
    }
  }
  return groups;
}
