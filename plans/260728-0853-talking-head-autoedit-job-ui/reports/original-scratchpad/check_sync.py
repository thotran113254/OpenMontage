import json
base=r"C:\Users\PC\AppData\Local\Temp\claude\D--CODE-WITH-AI-VIDEO-EDITOR-AI-AGENT\2e50444e-ceec-4f33-83b7-e7ab1620d735\scratchpad"
spec=json.load(open(base+r"\editor_spec.json",encoding="utf-8"))
wt=json.load(open(base+r"\user_transcript.json",encoding="utf-8"))["word_timestamps"]
print("Gemini caption lines:")
for l in spec["timeline"]["lines"][:12]:
    print(f"  G[{l['start']:6.2f}-{l['end']:6.2f}] {l['text']}")
print("\nWhisper words 0-22s (word@start):")
print("  "+" ".join(f"{w['word'].strip()}@{w['start']:.1f}" for w in wt if w['start']<22))
