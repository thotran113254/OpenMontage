import React, { useRef, useState } from "react";
import { ProjectSource } from "../api/client";

/**
 * Drag-drop upload with a REAL progress bar, one file at a time.
 *
 * `XMLHttpRequest`, not `fetch`: fetch has no upload progress event, and a
 * 200 MB video with no progress reads as a hung page. That is the entire reason
 * this component does not use the shared api client.
 *
 * Files are uploaded one per request rather than as one batch so a failure names
 * the file that failed and the others still land — losing four good 200 MB
 * uploads because the fifth had the wrong extension is the wrong trade.
 */

interface Progress {
  name: string;
  percent: number;
  status: "waiting" | "uploading" | "done" | "error";
  error?: string;
}

const ACCEPT = ".mp4,.mov,.m4v,.webm,.mkv,.avi";

const uploadOne = (
  projectId: string,
  file: File,
  onProgress: (percent: number) => void,
): Promise<ProjectSource[]> =>
  new Promise((resolve, reject) => {
    const form = new FormData();
    form.append("files", file, file.name);

    const request = new XMLHttpRequest();
    request.open("POST", `/api/projects/${projectId}/sources`);
    request.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(Math.round((event.loaded / event.total) * 100));
    };
    request.onload = () => {
      if (request.status < 200 || request.status >= 300) {
        reject(new Error(request.responseText || `HTTP ${request.status}`));
        return;
      }
      try {
        const body = JSON.parse(request.responseText) as {
          added: ProjectSource[];
          failed: { filename: string; error: string }[];
        };
        // The server reports per-file failures in a 200 response, so a rejected
        // file has to be surfaced here rather than assumed to be a success.
        if (body.failed?.length) {
          reject(new Error(body.failed[0].error));
          return;
        }
        resolve(body.added);
      } catch (error) {
        reject(error instanceof Error ? error : new Error(String(error)));
      }
    };
    request.onerror = () => reject(new Error("Mất kết nối khi upload"));
    request.send(form);
  });

export const SourceUploader: React.FC<{
  projectId: string;
  onUploaded: (sources: ProjectSource[]) => void;
  /** Compact: one button until user wants to add more (keeps list above the fold). */
  compact?: boolean;
}> = ({ projectId, onUploaded, compact = false }) => {
  const [items, setItems] = useState<Progress[]>([]);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [expanded, setExpanded] = useState(!compact);
  const inputRef = useRef<HTMLInputElement>(null);

  const send = async (files: File[]) => {
    if (!files.length) return;
    setBusy(true);
    setItems(files.map((file) => ({ name: file.name, percent: 0, status: "waiting" })));
    const added: ProjectSource[] = [];

    for (let index = 0; index < files.length; index += 1) {
      const file = files[index];
      const patch = (next: Partial<Progress>) =>
        setItems((current) =>
          current.map((item, position) => (position === index ? { ...item, ...next } : item)));
      patch({ status: "uploading" });
      try {
        const sources = await uploadOne(projectId, file, (percent) => patch({ percent }));
        added.push(...sources);
        patch({ status: "done", percent: 100 });
      } catch (error) {
        patch({ status: "error", error: String(error).slice(0, 200) });
      }
    }

    setBusy(false);
    if (added.length) onUploaded(added);
  };

  const pick = (fileList: FileList | null) => {
    if (fileList) void send(Array.from(fileList));
  };

  return (
    <div className={compact && !expanded && items.length === 0 ? "" : "card"}>
      {compact && !expanded && items.length === 0 ? (
        <button
          type="button"
          className="ghost"
          onClick={() => {
            setExpanded(true);
            window.setTimeout(() => inputRef.current?.click(), 0);
          }}
        >
          + Thêm video
        </button>
      ) : (
        <>
          <div className="row" style={{ alignItems: "center", marginBottom: 8 }}>
            <h3 style={{ margin: 0 }}>{compact ? "Thêm video" : "Upload video"}</h3>
            <span style={{ flex: 1 }} />
            {compact && (
              <button type="button" className="ghost small" onClick={() => setExpanded(false)}>
                Thu gọn
              </button>
            )}
          </div>
          <div
            onDragOver={(event) => {
              event.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(event) => {
              event.preventDefault();
              setDragging(false);
              pick(event.dataTransfer.files);
            }}
            onClick={() => inputRef.current?.click()}
            role="button"
            tabIndex={0}
            className={`dropzone ${dragging ? "on" : ""} ${compact ? "dropzone-compact" : ""}`}
            style={{ cursor: busy ? "wait" : "pointer" }}
            onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                inputRef.current?.click();
              }
            }}
          >
            <div style={{ fontSize: compact ? 14 : 15 }}>
              {busy ? "Đang upload…" : "Kéo file vào đây, hoặc bấm chọn"}
            </div>
            <div className="muted small" style={{ marginTop: 6 }}>
              Mỗi file = một video. mp4 / mov.
            </div>
          </div>
        </>
      )}
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPT}
        multiple
        style={{ display: "none" }}
        onChange={(event) => pick(event.target.files)}
      />

      {items.map((item) => (
        <div key={item.name} className="sample-row" style={{ marginTop: 8 }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div className="small" style={{ overflow: "hidden", textOverflow: "ellipsis" }}>
              {item.name}
            </div>
            <div
              style={{
                height: 6,
                borderRadius: 3,
                background: "var(--line)",
                marginTop: 4,
                overflow: "hidden",
              }}
            >
              <div
                style={{
                  width: `${item.percent}%`,
                  height: "100%",
                  background:
                    item.status === "error" ? "var(--accent)" : "var(--ok)",
                  transition: "width .15s linear",
                }}
              />
            </div>
            {item.error && <div className="error-text small">{item.error}</div>}
          </div>
          <span className="small muted" style={{ width: 56, textAlign: "right" }}>
            {item.status === "done" ? "xong" : item.status === "error" ? "lỗi" : `${item.percent}%`}
          </span>
        </div>
      ))}
    </div>
  );
};
