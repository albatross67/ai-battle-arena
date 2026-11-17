"""Build a self-running browser smoke test page for the replay viewer.

Usage (from repo root):  python tests/viewer_smoke.py
Then open output/viewer_smoke.html in a browser; the page title reports
SMOKE PASS/FAIL and a #smokeout <pre> lists each check plus measured numbers.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # run from anywhere

from arena.engine import Config, run_match
from arena.mapgen import GameMap
from arena.viewer import render_html

XSS_ID = '<img src=x onerror="window.__xss=1">'
QUOTED_ID = 'Bob & "Alice" \U0001f98a'
TICK_ID = "Z`${weird}`"


def scripted(decide, hat="RED"):
    def bot(obs, memory):
        if obs.get("phase") == "setup":
            return {"type": "hat", "color": hat}, memory
        return decide(obs), memory
    return bot


def build(out: Path) -> None:
    gm = GameMap(size=25, rocks=frozenset(),
                 spawns=((5, 5), (6, 5), (18, 18), (18, 19)), loot=())
    strategies = {  # sorted id order maps to spawn order: '<' < 'A' < 'B' < 'Z'
        XSS_ID: scripted(lambda o: {"type": "wait"}, hat="BLUE"),
        "A_normal": scripted(lambda o: {"type": "attack", "target": XSS_ID}, hat="GREEN"),
        QUOTED_ID: scripted(lambda o: {"type": "wait"}, hat="YELLOW"),
        TICK_ID: scripted(lambda o: {"type": "wait"}),
    }
    log = run_match(strategies, seed=5, config=Config(max_ticks=30), game_map=gm)
    render_html(log, out)
    html = out.read_text(encoding="utf-8")
    assert "</body></html>" in html
    out.write_text(html.replace("</body></html>", SMOKE + "</body></html>"), encoding="utf-8")
    print(f"built {out} ({len(log['frames']) - 1} ticks, winner {log['winner']!r})")


SMOKE = r"""
<script>
(function(){
 const A=window.__arena, cvs=document.getElementById("arena");
 const out=[];let fails=0;
 function t(name,fn){try{fn();out.push("PASS "+name);}catch(e){fails++;out.push("FAIL "+name+" :: "+e.message);}}
 function eq(a,b,m){if(a!==b)throw new Error((m||"")+" expected ["+b+"] got ["+a+"]");}
 function near(a,b,eps,m){if(Math.abs(a-b)>eps)throw new Error((m||"")+" expected ~"+b+" got "+a);}
 const speed=document.getElementById("speed");

 t("timing preserves sub-tick fraction across boundaries",()=>{
  speed.value="400";A.setIndex(0);A.start();A.advance(600);
  eq(A.index,1,"index");near(A.sub,0.5,1e-9,"sub");
  A.advance(1000);eq(A.index,4,"index after 1600ms");near(A.sub,0,1e-9,"sub after whole ticks");
  A.stop();});

 t("playback stops exactly on the final frame",()=>{
  speed.value="400";A.setIndex(A.frames.length-2);A.start();A.advance(4000);
  eq(A.index,A.frames.length-1,"index");eq(A.playing,false,"playing");});

 t("every speed option drives the tick duration",()=>{
  for(const pair of [["400",200],["150",75],["60",30]]){
   speed.value=pair[0];A.setIndex(0);A.start();A.advance(pair[1]);
   near(A.sub,0.5,1e-9,"speed "+pair[0]);A.stop();}
  speed.value="400";});

 t("scrubbing resets sub-tick and syncs UI",()=>{
  A.setIndex(5);eq(A.sub,0,"sub");
  eq(document.getElementById("scrub").value,"5","scrub");
  eq(document.getElementById("tick").textContent,String(A.frames[5].tick),"tick label");});

 t("hostile fighter names render as inert text",()=>{
  const names=Array.from(document.querySelectorAll(".row .nm")).map(n=>n.textContent);
  eq(names.length,A.fighters.length,"row count");
  A.fighters.forEach(id=>{if(names.indexOf(id)<0)throw new Error("missing "+id);});
  eq(window.__xss,undefined,"xss canary fired");
  eq(document.querySelector('img[src="x"]'),null,"injected img exists");
  eq(document.querySelectorAll("#roster img").length,A.fighters.length,"portrait count");});

 function cssOf(id){
  const p=A.frames[A.index].players[id];
  const cam=A.camera(),rect=cvs.getBoundingClientRect(),dev=cvs.width/rect.width;
  return [((p.x+.5)*cam.T*cam.s+cam.x)/dev,((p.y+.5)*cam.T*cam.s+cam.y)/dev];
 }
 t("click pick resolves fighters unzoomed",()=>{
  A.select(null);A.setIndex(0);
  const id=A.fighters[1],c=cssOf(id);eq(A.pick(c[0],c[1]),id);});

 t("click pick honors the zoomed follow camera",()=>{
  A.setIndex(0);A.select(A.fighters[0]);
  const other=A.fighters[1],c=cssOf(other);
  eq(A.pick(c[0],c[1]),other,"zoomed pick");A.select(null);});

 t("average draw cost stays under 16ms/frame",()=>{
  A.drawOnce(0,0); // warm sprite/floor caches and canvas resize
  const n=200,t0=performance.now();
  for(let k=0;k<n;k++)A.drawOnce(k%(A.frames.length-1),(k%10)/10);
  const avg=(performance.now()-t0)/n;
  out.push("  measured: "+avg.toFixed(2)+" ms/frame over "+n+" draws ("+(1000/avg).toFixed(0)+" fps headroom)");
  if(performance.memory){
   const m0=performance.memory.usedJSHeapSize;
   for(let k=0;k<n;k++)A.drawOnce(k%(A.frames.length-1),(k%10)/10);
   out.push("  js heap delta over "+n+" more draws: "+((performance.memory.usedJSHeapSize-m0)/1024).toFixed(0)+" KB");
  }
  if(avg>16)throw new Error("avg "+avg.toFixed(2)+"ms exceeds frame budget");});

 A.setIndex(0);
 const pre=document.createElement("pre");pre.id="smokeout";
 pre.textContent=(fails?("SMOKE FAIL ("+fails+")"):"SMOKE PASS")+"\n"+out.join("\n");
 document.body.insertBefore(pre,document.body.firstChild);
 document.title=fails?("SMOKE FAIL "+fails):"SMOKE PASS";
})();
</script>
"""


if __name__ == "__main__":
    build(Path("output/viewer_smoke.html"))
