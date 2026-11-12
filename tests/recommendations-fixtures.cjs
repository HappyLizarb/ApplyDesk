const http = require('node:http');
const crypto = require('node:crypto');

const description = 'Build reliable engineering systems with experienced colleagues. Required qualifications: current undergraduate study in computer science, Python programming and clear communication. Preferred qualifications: distributed systems coursework. Candidates may submit at most two applications across the company and its affiliates. Include your available internship dates in your resume.';
const privateText = '<aside>PRIVATE PROFILE private@example.test PRIVATE INBOX MESSAGE</aside>';

// This local SPA retains its authenticated document when recommendation links are followed.
const spa = `<script>
const initialBody=document.body.innerHTML, initialTitle=document.title;
window.fixtureSessionAlive=true;
document.addEventListener('click',async event=>{
  const anchor=event.target.closest('a[href^="/job-search/"],a[href^="/jobs/search-results/"]');if(!anchor)return;
  event.preventDefault();const response=await fetch(anchor.href);
  const html=new DOMParser().parseFromString(await response.text(),'text/html');
  history.pushState({},'',response.url);document.title=html.title;document.body.innerHTML=html.body.innerHTML;
  fetch('/telemetry',{method:'POST'}).catch(()=>{});fetch('/submit',{method:'POST'}).catch(()=>{});
  if(html.querySelector('[data-hydrate]'))document.body.innerHTML=await fetch('/hydrate-job',{method:'POST'}).then(response=>response.text());
  if(location.pathname.endsWith('/11466493'))fetch('/graphql',{method:'POST'}).catch(()=>{});
  if(location.pathname.endsWith('/666666'))fetch('/cdn-cgi/challenge-platform/private-nonce',{method:'POST'}).catch(()=>{});
});
addEventListener('popstate',()=>{document.title=initialTitle;document.body.innerHTML=initialBody;});
</script>`;

// Fixtures reproduce only DOM landmarks observed in the signed-in English source surfaces.
function feed(source) {
  const links = source === 'handshake' ? '<a href="/job-search/11466492">Engineering intern</a><a href="/job-search/11466493">Incomplete listing</a><a href="/job-search/11466494">Redirected listing</a>' :
    '<a href="/jobs/search-results/?currentJobId=4474551500&origin=JOBS_HOME">Engineering intern</a><a href="/jobs/view/4474551500/">Duplicate intern</a><a href="/jobs/search-results/?currentJobId=4474551501">Second intern</a>';
  const heading = source === 'handshake' ? 'Job picks for software developers and engineers in San Francisco' : 'Jobs that match your profile';
  return `<title>Recommended jobs</title>${privateText}<main><section><header><div><h2>${heading}</h2></div></header>${links}<button onclick="fetch('/save',{method:'POST'})">Save</button></section>
    <section><h2>Other content</h2><a href="/job-search/999999">Unrelated school collection</a></section></main>${spa}`;
}

// A tiny text-only websocket fixture checks forwarding and closure without external dependencies.
function attachSocketFixture(server) {
  const messages = [], sockets = new Set();
  server.on('upgrade', (request, socket) => {
    sockets.add(socket);
    const accept = crypto.createHash('sha1').update(request.headers['sec-websocket-key'] + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest('base64');
    socket.write('HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: ' + accept + '\r\n\r\n');
    socket.on('data', bytes => {
      if ((bytes[0] & 15) === 8) { socket.end(Buffer.from([136, 0])); return; }
      const length = bytes[1] & 127;
      if (length >= 126 || bytes.length < length + 6) return;
      const text = Buffer.from(bytes.subarray(6, 6 + length).map((value, index) => value ^ bytes[2 + index % 4])).toString();
      messages.push(text);socket.write(Buffer.concat([Buffer.from([129, Buffer.byteLength(text)]), Buffer.from(text)]));
    });
    socket.on('close', () => sockets.delete(socket));
    socket.on('error', () => sockets.delete(socket));
  });
  return {messages, sockets};
}

function handshake(id, folded = true) {
  const body = folded ? `<p>Job description introduction.</p><p hidden id="rest">${description}</p><button type="button" class="view-more-button" aria-label="Show more (Engineering Intern)" onclick="this.disabled=true;setTimeout(()=>{document.querySelector('#rest').hidden=false;this.remove()},250)">Show more</button>` : `<p>${description}</p>`;
  return `<title>Engineering Intern | Handshake</title>${privateText}<div data-hook="right-content"><header>
    <a href="/e/123" aria-label="Acme Systems"><img alt="Acme Systems logo"></a><a href="/jobs/${id}?searchId=abc"><h1>Engineering Intern</h1></a></header>
    <section><div><h3>At a glance</h3></div><p>Onsite, based in San Francisco, CA</p><p>Internship</p><p data-hook="work-auth-title">US work authorization required</p></section>
    <section><div><h3>Job description</h3></div>${body}</section><div>PRIVATE PERSONAL FIT BADGES</div>
    <button onclick="fetch('/submit',{method:'POST'})">Apply</button></div><script>fetch('/telemetry',{method:'POST'}).catch(()=>{})</script>`;
}

function linkedin(anchorTitle = false, id = '4474551500') {
  const title = anchorTitle ? `<a href="/jobs/view/${id}/">Engineering Intern</a>` : '<p>Engineering Intern</p>';
  return `<title>${anchorTitle ? 'Search | LinkedIn' : 'Engineering Intern | Acme Systems | LinkedIn'}</title>${privateText}<main><article>
    <header><a href="/company/acme/life/"><div aria-label="Company, Acme Systems.">Acme Systems</div></a>${title}<p>San Jose, CA · 2 weeks ago · 12 people clicked apply</p></header>
    <a href="/jobs/view/${id}/">Internship</a><a href="/jobs/view/${id}/">I’m interested</a>
    <a aria-label="Apply on company website" href="/safety/go/?url=https%3A%2F%2Fjobs.example.test%2Fsearch%2F123">Apply</a><button onclick="fetch('/save',{method:'POST'})">Save</button>
    <section id="JobDetails_AboutTheJob_${id}"><h2>About the job</h2><p>${description}</p></section></article></main>
    <aside><p>Engineering Intern</p></aside><script>window.fixtureCspEvalBlocked=false;try{new Function('return true')()}catch{window.fixtureCspEvalBlocked=true}fetch('/telemetry',{method:'POST'}).catch(()=>{})</script>`;
}

// The server counts real writes, including deceptive controls, to verify the network boundary.
async function fixtureServer() {
  const writes = [], visits = [];
  const server = http.createServer((request, response) => {
    const url = new URL(request.url, 'http://fixture.test');
    visits.push(url.pathname);
    response.setHeader('Content-Type', 'text/html; charset=utf-8');
    response.setHeader('Content-Security-Policy', "script-src 'self' 'unsafe-inline'; object-src 'none'");
    if (request.method === 'POST') writes.push(url.pathname);
    if (url.pathname === '/manual-pending') return;
    if (url.pathname === '/manual-login') {
      response.writeHead(302, {'Set-Cookie': 'signed=1; Path=/; Max-Age=3600', Location: url.searchParams.get('source') === 'linkedin' ? '/jobs/' : '/home'});
      return response.end();
    }
    if (url.pathname === '/home' || url.pathname === '/jobs/') {
      const source = url.pathname === '/home' ? 'handshake' : 'linkedin';
      if (request.headers.cookie?.includes('signed=1')) return response.end(feed(source));
      return response.end(`<title>Sign in</title><form method="post" action="/manual-login?source=${source}"><label>Password<input type="password"></label><button>Sign in</button></form>`);
    }
    if (url.pathname === '/job-search/11466492') return response.end('<title>Loading job</title><main data-hydrate></main><script>fetch("/hydrate-job",{method:"POST"}).then(response=>response.text()).then(html=>document.body.innerHTML=html)</script>');
    if (url.pathname === '/hydrate-job') return setTimeout(() => response.end(handshake('11466492')), 250);
    if (url.pathname === '/job-search/11466493') return response.end('<title>Listing</title><script>fetch("/graphql",{method:"POST"}).catch(()=>{})</script>');
    if (url.pathname === '/job-search/11466494') { response.writeHead(302, {Location: '/job-search/11466999'}); return response.end(); }
    if (url.pathname === '/job-search/11466999') return response.end(handshake('11466999', false));
    if (url.pathname === '/jobs/view/4474551500/') return response.end(linkedin());
    if (url.pathname === '/jobs/search-results/') return response.end(linkedin(true, url.searchParams.get('currentJobId')));
    if (url.pathname === '/job-search/555555') return;
    if (url.pathname === '/job-search/666666') return response.end('<title>Just a moment...</title>');
    response.end('<title>Fixture</title>');
  });
  const websocket = attachSocketFixture(server);
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  return {server, writes, visits, websocket, origin: `http://127.0.0.1:${server.address().port}`};
}

module.exports = {fixtureServer, handshake, linkedin, feed, description};
