import React, { useState } from "react";

type AnyEvent = Record<string, any>;

const EDITABLE_TEXT_FIELDS = ["text", "highlight", "title", "kicker", "subtitle", "badge"];
const EDITABLE_NUMBER_FIELDS = ["at", "end", "xPct", "yPct", "fontSize", "rotation", "scale", "volume"];

/**
 * Direct edits to the resolved timeline — no model call, no re-render. Saving
 * writes a new props version, so a hand tweak is a first-class version you can
 * come back from, not an untracked overwrite.
 */
export const EventInspector: React.FC<{
  events: AnyEvent[];
  onChange: (events: AnyEvent[]) => void;
  onSeek: (seconds: number) => void;
}> = ({ events, onChange, onSeek }) => {
  const [filter, setFilter] = useState("all");
  const [openIndex, setOpenIndex] = useState<number | null>(null);

  const types = Array.from(new Set(events.map((e) => e.type))).sort();
  const visible = events
    .map((event, index) => ({ event, index }))
    .filter(({ event }) => filter === "all" || event.type === filter);

  const update = (index: number, key: string, value: unknown) => {
    const next = events.map((event, i) => (i === index ? { ...event, [key]: value } : event));
    onChange(next);
  };

  const remove = (index: number) => {
    onChange(events.filter((_, i) => i !== index));
    setOpenIndex(null);
  };

  return (
    <div>
      <div className="row" style={{ marginBottom: 10 }}>
        <select value={filter} onChange={(e) => setFilter(e.target.value)}>
          <option value="all">Tất cả ({events.length})</option>
          {types.map((type) => (
            <option key={type} value={type}>
              {type} ({events.filter((e) => e.type === type).length})
            </option>
          ))}
        </select>
      </div>

      <div className="event-list">
        {visible.map(({ event, index }) => (
          <div key={index} className={`event ${event.type}`}>
            <div className="head" onClick={() => setOpenIndex(openIndex === index ? null : index)}>
              <span className="at">{Number(event.at).toFixed(2)}s</span>
              <span className="type">{event.type}</span>
              <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {event.text || event.title || event.name || ""}
              </span>
              <button className="ghost" style={{ padding: "2px 8px" }}
                      onClick={(e) => { e.stopPropagation(); onSeek(Number(event.at)); }}>
                ▶
              </button>
            </div>

            {openIndex === index && (
              <div className="body">
                {EDITABLE_TEXT_FIELDS.filter((f) => event[f] !== undefined).map((field) => (
                  <div key={field}>
                    <label>{field}</label>
                    <input value={event[field] ?? ""} onChange={(e) => update(index, field, e.target.value)} />
                  </div>
                ))}
                {Array.isArray(event.bullets) && (
                  <div>
                    <label>bullets (mỗi dòng một ý)</label>
                    <textarea
                      value={event.bullets.join("\n")}
                      onChange={(e) => update(index, "bullets", e.target.value.split("\n"))}
                    />
                  </div>
                )}
                <div className="row">
                  {EDITABLE_NUMBER_FIELDS.filter((f) => event[f] !== undefined).map((field) => (
                    <div key={field}>
                      <label>{field}</label>
                      <input type="number" step="0.01" value={event[field]}
                             onChange={(e) => update(index, field, Number(e.target.value))} />
                    </div>
                  ))}
                </div>
                {event.highlightColor !== undefined && (
                  <div>
                    <label>highlightColor</label>
                    <input value={event.highlightColor}
                           onChange={(e) => update(index, "highlightColor", e.target.value)} />
                  </div>
                )}
                <button className="ghost error-text" onClick={() => remove(index)}>
                  Xoá event này
                </button>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
};
