import React from "react";
import { registerRoot, Composition, AbsoluteFill, Sequence, Img, OffthreadVideo, Audio, staticFile, useCurrentFrame, interpolate } from "remotion";
import edit from "../edit.json";
const C = { bg: "#F4F1EC", ink: "#17191B", mint: "#249D7E", amber: "#A06D17" };
const fade = (f, start) => interpolate(f, [start, start + 8], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
function Shell({ title, kicker, children }) {
  return <AbsoluteFill style={{ background: C.bg, color: C.ink, fontFamily: "Segoe UI, Arial", padding: "54px 84px" }}><div style={{ display: "flex", justifyContent: "space-between", fontSize: 24, letterSpacing: 3, color: C.mint }}><b>HISAAB</b><span>{kicker || "REFUND CONSISTENCY ENGINE"}</span></div><h1 style={{ fontSize: 54, margin: "22px 0 28px", fontWeight: 650 }}>{title}</h1>{children}<div style={{ position: "absolute", bottom: 35, left: 84, fontSize: 22, color: "#555" }}>Ek Refund. Sau Nakhre.</div></AbsoluteFill>;
}
function Problem() {
  const f = useCurrentFrame();
  const n = f < 360 ? 300 : f < 420 ? 600 : 900;
  return <Shell title={f < 480 ? "One refund." : "One refund. Three notifications."} kicker="ILLUSTRATION"><div style={{ display: "flex", gap: 65, marginTop: 55, alignItems: "center" }}><div style={{ background: "white", border: "2px solid #ddd8cf", borderRadius: 24, padding: 55, width: 420, height: 430 }}><div style={{ fontSize: 25, color: "#555" }}>REFUND RECEIPT · R1</div><div style={{ fontSize: 112, marginTop: 45 }}>₹300</div><div style={{ fontSize: 28, color: C.mint, marginTop: 28 }}>One refund record</div></div><div style={{ flex: 1 }}>{["a1", "a2", "a3"].map((a, i) => <div key={a} style={{ opacity: fade(f, 120 + i * 60), transform: `translateX(${(1 - fade(f, 120 + i * 60)) * 35}px)`, padding: "22px 32px", background: "#DEEEE6", borderRadius: 14, marginBottom: 16, fontSize: 32, display: "flex", justifyContent: "space-between" }}><b>Success · E8</b><span>attempt {a}</span></div>)}<div style={{ opacity: fade(f, 300), marginTop: 28, padding: 28, borderTop: "2px solid #ccc" }}><span style={{ fontSize: 27 }}>Naive record</span><b style={{ float: "right", fontSize: 68, color: C.amber }}>₹{n}</b><p style={{ fontSize: 22, marginBottom: 0 }}>Recorded amount · no money moves</p></div></div></div></Shell>;
}
function Media({ scene }) {
  const f = useCurrentFrame();
  const m = scene.media;
  return <Shell title={scene.title} kicker={scene.label}><div style={{ position: "relative", height: 735, borderRadius: 22, overflow: "hidden", background: "#101416", border: "2px solid #d6d2cb" }}>{m ? m.match(/\.(mp4|mov|webm)$/i) ? <OffthreadVideo src={staticFile("media/" + m)} muted startFrom={Math.round((scene.trimStart || 0) * 30)} style={{ width: "100%", height: "100%", objectFit: "contain" }} /> : <Img src={staticFile("media/" + m)} style={scene.crop ? { position: "absolute", width: 2156, height: 1516, maxWidth: "none", left: -404, top: -(f < Math.round(scene.duration * 15) ? scene.crop[0] : scene.crop[1]) * 1.684 } : { width: "100%", height: "100%", objectFit: "contain" }} /> : <div style={{ padding: 65, color: "white", fontSize: 38 }}>ASSEMBLY — REQUIRED EVIDENCE MISSING<p>{scene.missing}</p></div>}{scene.note && <div style={{ position: "absolute", bottom: 20, left: 24, right: 24, background: "#F4F1EC", padding: "12px 20px", borderRadius: 9, fontSize: 25, color: C.ink }}>{scene.note}</div>}</div></Shell>;
}
function Architecture() {
  return <Shell title="A backend for refund consistency" kicker="PYTHON · FASTAPI · SQLITE"><div style={{ display: "flex", gap: 25, marginTop: 45 }}>{[["Your application backend", "Register refunds + normalized observations", "Bearer API"], ["Signed Hyperswitch sample", "HMAC-SHA512 signature verification", "Separate adapter"]].map((x) => <div key={x[0]} style={{ flex: 1, padding: 30, borderRadius: 18, background: "#DEEEE6", fontSize: 28 }}><b>{x[0]}</b><p>{x[1]}</p><small>{x[2]}</small></div>)}</div><div style={{ textAlign: "center", fontSize: 42, margin: 30 }}>↓</div><div style={{ background: "white", padding: 35, borderRadius: 18, fontSize: 32, textAlign: "center" }}>HISAAB / FastAPI → validation + refund rules → SQLite transaction</div><p style={{ fontSize: 28, textAlign: "center" }}>Refund status + confirmed / review amounts</p><p style={{ fontSize: 22, textAlign: "center" }}>Contract documentation · public integration API is not enabled</p></Shell>;
}
function Evidence({ scene }) {
  const f = useCurrentFrame();
  const lines = scene.text.split("\n");
  const text = scene.label.includes("TEST SOURCE") ? (f < 120 ? lines.slice(0, 17) : lines.slice(17)).join("\n") : scene.text;
  return <Shell title={scene.title} kicker={scene.label}><div style={{ background: "#101416", color: "#eee", borderRadius: 22, padding: "35px 42px", height: 735, overflow: "hidden" }}><pre style={{ fontFamily: "Consolas, monospace", fontSize: scene.fontSize || 26, lineHeight: 1.4, whiteSpace: "pre-wrap", margin: 0 }}>{text}</pre></div></Shell>;
}
function Close() {
  return <Shell title="Every refund accounted for."><div style={{ marginTop: 100, fontSize: 100, fontWeight: 700 }}>HISAAB<span style={{ color: C.mint }}> .</span></div><p style={{ fontSize: 42 }}>Ek Refund. Sau Nakhre.</p><p style={{ fontSize: 31, marginTop: 60 }}>github.com/sevenhiremath/refund-consistency-lab</p><p style={{ fontSize: 31 }}>refund-consistency-lab.onrender.com</p><p style={{ fontSize: 24 }}>Built as Refund Consistency</p></Shell>;
}
function Film() {
  const f = useCurrentFrame();
  return <AbsoluteFill>{edit.scenes.map((s, i) => <Sequence key={i} from={Math.round(s.start * 30)} durationInFrames={Math.round(s.duration * 30)}>{s.type === "problem" ? <Problem /> : s.type === "architecture" ? <Architecture /> : s.type === "close" ? <Close /> : s.type === "evidence" ? <Evidence scene={s} /> : <Media scene={s} />}</Sequence>)}{edit.voice && <Audio src={staticFile("media/" + edit.voice)} />}{edit.music && <Audio src={staticFile("media/" + edit.music)} volume={(x) => 0.045 * Math.max(0, Math.min(1, x / 45, (3510 - x) / 60))} />}{edit.captions?.filter((c) => f >= c.start * 30 && f < c.end * 30).map((c, i) => <div key={i} style={{ position: "absolute", bottom: 68, left: 180, right: 180, textAlign: "center", background: "#17191Bee", color: "white", padding: "12px 22px", fontSize: 31, borderRadius: 10 }}>{c.text}</div>)}</AbsoluteFill>;
}
registerRoot(() => <Composition id="Hisaab" component={Film} durationInFrames={3510} fps={30} width={1920} height={1080} />);
