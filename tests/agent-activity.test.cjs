const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');

// Test the displayed logical agent count without launching browsers or calling providers.
(async()=>{
  const source=fs.readFileSync(path.join(__dirname,'../web/agent-activity.js'),'utf8');
  const {groupAgentRuns}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
  const run=(id,role,turn_id,status='completed')=>({id,role,turn_id,status,model:'test'});
  const input=[run('1','master','turn-a'),run('2','application','turn-a'),run('3','master','turn-a','running'),
    run('4','application_auditor','turn-a'),run('5','master','turn-b')];
  const grouped=groupAgentRuns(input);
  assert.equal(grouped.length,4);
  assert.equal(grouped[0].steps,2);
  assert.equal(grouped[0].status,'running');
  assert.equal(grouped[1].role,'application');
  assert.equal(input[0].steps,undefined);
  assert.equal(groupAgentRuns([run('old1','master'),run('old2','master'),run('old3','master')]).length,1);
  assert.equal(groupAgentRuns([]).length,0);
  const single=groupAgentRuns([run('a','master','turn-c'),run('b','master','turn-c'),run('c','master','turn-c')]);
  assert.equal(single.length,1);
  assert.equal(single[0].steps,3);
  console.log('Agent activity groups sequential single-agent steps and retains historical run records.');
})().catch(error=>{console.error(error);process.exitCode=1;});
