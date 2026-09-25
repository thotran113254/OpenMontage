import React from "react";
import { FlowSteps } from "../components/flow-steps";

type Stage = {
  key: string;
  name: string;
  llm: "none" | "text" | "text+image-conditional" | "image+audio-off";
  desc: string;
  model: string;
  note?: string;
};

// Nguồn: lib/talking_head_edit/job_store.py STAGES + từng file stages/*.py.
// Mọi lệnh gọi model đều đi qua director_client.chat_json / chat_with_images /
// chat_with_audio — tất cả rơi về AUTOEDIT_DIRECTOR_MODEL (.env) nếu job không
// tự chỉ định model khác.
const STAGES: Stage[] = [
  { key: "probe", name: "Probe", llm: "none", model: "—",
    desc: "Đọc metadata file (fps, kích thước, thời lượng) bằng ffprobe. Cơ học thuần, không gọi model." },
  { key: "transcribe", name: "Transcribe", llm: "none", model: "ElevenLabs Scribe v2 (mặc định) / Whisper local (dự phòng)",
    desc: "Tách lời nói thành các từ có timestamp (word spine). Không phải LLM sinh nội dung — đây là ASR (nhận dạng giọng nói), không phải director." },
  { key: "select", name: "Select", llm: "text", model: "Director model",
    desc: "Khi có ≥2 nguồn (nhiều take), hỏi model chọn take nào giữ cho từng đoạn trùng nội dung. Chỉ 1 nguồn → bỏ qua hoàn toàn bước gọi model." },
  { key: "direct", name: "Direct", llm: "text", model: "Director model",
    desc: "Bước “đạo diễn” chính: 1 lệnh cấu trúc (card, keyword, sfx, cold-open, endcard, bgm, grade, đề xuất cắt) + N lệnh caption theo từng nhóm từ, chạy song song. Đây là bước tốn token nhất." },
  { key: "audit", name: "Audit", llm: "text+image-conditional", model: "Director model (hoặc verifier_model riêng nếu được set)",
    desc: "Kiểm lại đề xuất cắt của Direct bằng 1 lệnh verifier độc lập (văn bản). Đoạn nào verifier “không chắc” mới được xem lại lần 2 kèm ảnh dạng sóng âm — đây là điểm DUY NHẤT trên đường chạy mặc định cần model biết nhìn ảnh." },
  { key: "calibrate", name: "Calibrate", llm: "image+audio-off", model: "Director model",
    desc: "So màu / so tiếng trên vài giây mẫu rồi để model chấm bản nào tốt nhất.", note: "TẮT theo mặc định — blind-test cho thấy model chấm theo nhãn chứ không theo nội dung thật. Chỉ chạy khi bật cờ calibrate_grade/calibrate_audio thủ công." },
  { key: "resolve", name: "Resolve", llm: "none", model: "—",
    desc: "Áp toàn bộ quyết định (cắt, chỉnh màu, chọn nhạc/âm thanh) vào file trung gian bằng ffmpeg. Cơ học thuần." },
  { key: "render", name: "Render", llm: "none", model: "—",
    desc: "Remotion dựng video cuối từ spec đã audit — chỉ đọc dữ liệu, không gọi model." },
  { key: "verify", name: "Verify", llm: "none", model: "—",
    desc: "Kiểm tra file render ra (thời lượng, có audio, độ phân giải…) — mechanical, không gọi model." },
];

const LLM_BADGE: Record<Stage["llm"], string> = {
  none: "Không gọi model",
  text: "Gọi model — chỉ văn bản",
  "text+image-conditional": "Gọi model — văn bản, + ảnh khi cần xem lại",
  "image+audio-off": "Gọi model — ảnh + âm thanh (tắt mặc định)",
};

export const HowItWorksPage: React.FC = () => {
  return (
    <div className="stack">
      <div className="card">
        <button className="ghost small" onClick={() => { window.location.hash = "/projects"; }}>
          ← Về trang chính
        </button>
        <h2 style={{ marginTop: 10 }}>Hướng dẫn làm short</h2>
        <div style={{ margin: "12px 0" }}>
          <FlowSteps
            steps={[
              { id: "p", label: "Nhà sáng tạo → project", state: "done" },
              { id: "u", label: "Upload video", state: "done" },
              { id: "t", label: "Chọn mẫu & chỉnh", state: "now" },
              { id: "x", label: "Xuất short", state: "todo" },
            ]}
          />
        </div>
        <p className="muted small">
          1. Mở video → <b>Chạy dựng short</b> (xem trước, chưa ra MP4).
          2. Vào trang bản dựng xem khung → bấm <b>Xuất MP4</b> khi ổn.
          Kết quả cũng hiện tab <b>Kết quả</b> của project.
        </p>
      </div>

      <div className="card">
        <h3>Model dùng ở đâu</h3>
        <p className="small">
          Mọi lệnh gọi AI trong pipeline — dù là Select, Direct, hay Audit — đều đi qua <b>1 gateway
          OpenAI-compatible duy nhất</b> (<code>director_client.py</code>), cấu hình bằng biến môi trường trong{" "}
          <code>.env</code>:
        </p>
        <ul className="small">
          <li><code>AUTOEDIT_DIRECTOR_MODEL</code> — model mặc định khi job không tự chỉ định (ví dụ hiện tại: <code>ag/gemini-3.7-flash-high</code> qua 9router).</li>
          <li><code>NINE_ROUTER_BASE_URL</code> / <code>NINE_ROUTER_API_KEY</code> — gateway dùng chung cho mọi model qua router (Gemini, GPT…), có thể override riêng bằng <code>AUTOEDIT_DIRECTOR_BASE_URL</code> / <code>AUTOEDIT_DIRECTOR_API_KEY</code> để trỏ thẳng sang API gốc của hãng khác (vd DeepSeek).</li>
        </ul>
        <p className="small">
          Mỗi job có thể tự ghi đè model riêng (CLI <code>--model</code>, hoặc field <code>options.model</code> khi
          gọi API tạo job) — không ghi đè thì rơi về <code>AUTOEDIT_DIRECTOR_MODEL</code>. Stage <code>Audit</code> có
          thêm 1 lớp: ô "Model kiểm cắt (verifier)" cho phép set <code>verifier_model</code> riêng
          — để trống thì dùng chung <code>AUTOEDIT_DIRECTOR_MODEL</code> (mặc định Gemini 3.7).
        </p>
        <p className="small">
          Mỗi lệnh gọi có tối đa 3 lần thử lại khi lỗi mạng/JSON hỏng, và tự cộng dồn chi phí
          (<code>cost_usd</code>) vào job nếu bạn cấu hình giá tiền/1M token.
        </p>
      </div>

      <div className="card">
        <h3>Các bước (stage) theo đúng thứ tự chạy</h3>
        <table>
          <thead>
            <tr>
              <th>Stage</th>
              <th>Model / nguồn AI</th>
              <th>Loại input model nhận</th>
              <th>Mô tả</th>
            </tr>
          </thead>
          <tbody>
            {STAGES.map((s) => (
              <tr key={s.key}>
                <td><b>{s.name}</b></td>
                <td className="small">{s.model}</td>
                <td><span className="badge" style={{ background: "#33415580" }}>{LLM_BADGE[s.llm]}</span></td>
                <td className="small">
                  {s.desc}
                  {s.note && <div className="warn-text" style={{ marginTop: 4 }}>{s.note}</div>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="card">
        <h3>Chỗ nào bắt buộc model phải "nghe" hoặc "nhìn"</h3>
        <ul className="small">
          <li><b>Ảnh (bắt buộc, trên đường chạy mặc định):</b> Audit → xem lại đoạn cắt "không chắc" bằng ảnh dạng
            sóng âm (<code>cut_verifier.look_again</code>). Chỉ kích hoạt khi lớp verifier văn bản không quyết được.</li>
          <li><b>Ảnh (tuỳ chọn, tắt mặc định):</b> Calibrate — so màu giữa các bản chỉnh màu.</li>
          <li><b>Âm thanh (tuỳ chọn, tắt mặc định):</b> Calibrate — so chất lượng tiếng; và 2 công cụ debug thủ công
            (<code>--judge-audio</code>, <code>--check-hearing</code>) không nằm trong pipeline tự động.</li>
        </ul>
        <p className="small muted">
          Vì vậy một model chỉ biết đọc/viết văn bản + nhìn ảnh (không nghe được audio) vẫn chạy được toàn bộ
          pipeline MẶC ĐỊNH — chỉ hỏng khi ai đó chủ động bật Calibrate hoặc chạy 2 công cụ debug kia mà quên đổi
          model cho chúng.
        </p>
      </div>
    </div>
  );
};
