# BGM Library — 12 bản / 5 nhóm cho video ngắn talking-head (AI/chatbot/business)

Kho nhạc nền của auto-editor. 12 file placeholder (synth) đã có trong `remotion-composer/public/` — tạo bản xịn bằng công cụ online (Suno/Udio/Stable Audio) rồi **thay file cùng tên** là pipeline dùng ngay, không sửa code. Gemini director tự chọn bản theo mood nội dung (hoặc set tay trong props).

## Nguyên tắc chung (áp cho MỌI bản)

Video là **voice-first** (giọng nói là chủ thể). Nhạc phải:

- **KHÔNG vocal, không giai điệu chủ đạo** — chỉ là "bed" đệm
- **Chừa dải mid 1–4 kHz** cho giọng: nặng bass + percussion nhẹ + pad, không lead chiếm mid
- **Loop liền mạch**: không intro/outro, không build-up/drop — năng lượng ĐỀU
- 60–120s, instrumental 100%, xuất MP3 320kbps/WAV, đặt đúng tên rồi bỏ vào `remotion-composer/public/`
- Renderer mix ở volume 0.10–0.15 (clamp trần 0.3)

Suno: thêm tag `[Instrumental]`. Stable Audio: dán prompt + duration 90s.

## Quy tắc vận hành kho

1. **Luân phiên trong nhóm** — 2 video cùng dạng liên tiếp phải khác bản nhưng cùng nhóm → chất âm kênh nhất quán
2. **Nền tảng**: kho này cho YouTube Shorts/Facebook; TikTok muốn đẩy viral thì cân nhắc trending sound của nền tảng
3. **Refresh 2–3 bản/quý** — thay bản ít dùng, giữ nguyên tên file/nhóm

---

## NHÓM 1 — EXPLAINER (video tips/listicle — dạng chính, ~50%)

### 1. `bgm_upbeat_bounce.mp3` — 104 BPM, C major, tươi nảy (mặc định)
> Upbeat marimba pop background music for an educational explainer video, 104 BPM, C major. Bouncy marimba and xylophone plucks, soft muted bass, light finger snaps and shaker, warm subtle pad. Cheerful, optimistic, clean and minimal. No vocals, no lead melody hooks, no brass. Instrumental loop with constant energy, no intro, no drop, seamless loop, mid-range left open for a voiceover.

### 2. `bgm_clean_explainer.mp3` — 100 BPM, D major, trung tính sạch
> Clean neutral corporate pop background bed, 100 BPM, D major. Soft piano chords, round warm bass, tight minimal drums with gentle hi-hats, airy pad. Professional, friendly, unobtrusive. No vocals, no melody hooks, sparse mid-range reserved for narration. Constant energy, seamless loop, no intro or build-ups.

### 3. `bgm_playful_pop.mp3` — 108 BPM, E major, tinh nghịch
> Playful staccato pop background music for a fun tips video, 108 BPM, E major. Perky pizzicato strings and plucky synth, bouncy bass, claps and shaker, tiny bell accents kept quiet. Lighthearted, witty, energetic but not busy. No vocals, no lead melody, mids left open for a voiceover. Seamless loop, steady groove, no drop.

## NHÓM 2 — TECH / AI (đúng niche kênh, ~25%)

### 4. `bgm_tech_pulse.mp3` — 100 BPM, D lydian, tối giản
> Minimal tech corporate background music, 100 BPM, D lydian. Clean digital plucks, soft analog pulse bass, airy pads, subtle glitch percussion and gentle clicks, spacious and precise. Futuristic, intelligent, calm confidence. No vocals, no melody hooks, very sparse mid-range for voiceover. Constant hypnotic pulse, seamless loop, no build-ups.

### 5. `bgm_data_groove.mp3` — 102 BPM, F# minor, groove đều
> Steady tech house groove background bed, 102 BPM, F sharp minor. Rolling filtered bassline, tight crisp hats, muted synth chord stabs, soft white-noise texture. Focused, modern, productive energy like a coding montage. No vocals, no lead synth, mid frequencies kept clear for speech. Even dynamics throughout, seamless loop.

### 6. `bgm_future_calm.mp3` — 88 BPM, C sus2, thoáng chậm
> Calm futuristic ambient background music, 88 BPM, C major suspended chords. Slow airy synth pads, sparse soft plucks, barely-there kick pulse, gentle shimmer. Serene, visionary, spacious and minimal. No vocals, no melody, almost empty mid-range for narration. Constant low energy, seamless loop, no swells.

## NHÓM 3 — HIGH ENERGY (cảnh báo/CTA/nhịp nhanh, ~15%)

### 7. `bgm_energy_drive.mp3` — 118 BPM, F major, house dồn
> Energetic minimal house background track for fast-paced social media video, 118 BPM, F major. Punchy four-on-the-floor kick, driving side-chained bass, crisp hi-hats, short plucky synth stabs, rising white-noise sweeps kept subtle. Confident, urgent, modern. No vocals, no big drop, no melodic lead — steady driving groove only, mids left clear for narration. Seamless loop.

### 8. `bgm_urgent_percussive.mp3` — 122 BPM, D minor, gấp gáp
> Urgent percussive background track for a warning-style short video, 122 BPM, D minor. Dense tom and kick pattern, tight snare accents, dark pulsing bass, tense minimal string stabs kept low. Suspenseful, driving, high alert without being cinematic-epic. No vocals, no melody line, mids open for a voiceover. Constant tension, seamless loop, no drop.

## NHÓM 4 — CHILL / TÂM SỰ (~5%)

### 9. `bgm_lofi_chill.mp3` — 82 BPM, A minor, ấm bụi
> Lo-fi chill hip-hop background beat, 82 BPM, A minor. Dusty mellow Rhodes chords, vinyl crackle, soft boom-bap drums with lazy swing hats, deep round sub bass, warm tape saturation. Relaxed, intimate, nostalgic. No vocals, no lead instruments, sparse arrangement leaving space for spoken voice. Seamless loop, constant low energy.

### 10. `bgm_lofi_night.mp3` — 78 BPM, E minor, đêm trầm
> Late-night moody lo-fi background beat, 78 BPM, E minor. Dark soft electric piano, rain-like vinyl noise, slow heavy boom-bap drums, deep sub bass, muffled lowpassed texture. Reflective, introspective, calm melancholy. No vocals, no melody hooks, very sparse mids for narration. Even quiet energy, seamless loop.

## NHÓM 5 — WARM / TRUYỀN CẢM HỨNG (~5%)

### 11. `bgm_warm_story.mp3` — 92 BPM, G major, acoustic ấm
> Warm acoustic folk-pop background bed, 92 BPM, G major. Soft fingerpicked acoustic guitar, gentle piano chords, light kick and brushed snare, warm upright-style bass, subtle strings pad. Heartfelt, hopeful, sincere. No vocals, no lead melody, gentle constant dynamics for narration underneath. Seamless loop, no intro or outro.

### 12. `bgm_hopeful_lift.mp3` — 95 BPM, C major, nâng đỡ
> Uplifting hopeful pop background music for an inspirational closing, 95 BPM, C major. Bright piano chords, warm swelling pad kept soft, steady light drums, round bass, sparse glockenspiel accents. Optimistic, encouraging, forward-moving. No vocals, no melody hooks, no big crescendo — steady lift, mids clear for speech. Seamless loop.

---

## Cách dùng trong pipeline

```jsonc
// Gemini director tự chọn, hoặc set tay trong mona_timeline_props.json:
"bgm": { "name": "bgm_upbeat_bounce.mp3", "volume": 0.12 }
```

## Checklist nghiệm thu file từ công cụ online

- [ ] Instrumental 100% (không vocal, kể cả humming)
- [ ] 20s đầu + 20s cuối: năng lượng đều, không fade/intro/drop
- [ ] Đè thử lên giọng nói: không tranh dải mid
- [ ] Đổi đúng tên + copy vào `remotion-composer/public/` (ghi đè)
