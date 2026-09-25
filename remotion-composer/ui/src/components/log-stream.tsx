import React from "react";
import { ProgressEvent } from "../api/client";
import { useStickToBottom } from "../lib/use-stick-to-bottom";
import { ScrollToBottomPill } from "./scroll-to-bottom-pill";

export const LogStream: React.FC<{ events: ProgressEvent[] }> = ({ events }) => {
  const { ref, contentRef, pinned, pendingCount, onScroll, onKeyDown, scrollToBottom } =
    useStickToBottom<HTMLDivElement, HTMLDivElement>(events.length);

  return (
    <div className="scroll-pane">
      <div
        className="log"
        ref={ref}
        tabIndex={0}
        role="log"
        aria-label="Nhật ký chạy"
        onScroll={onScroll}
        onKeyDown={onKeyDown}
      >
        <div ref={contentRef}>
          {events.length === 0 && <div className="muted">Chưa có log.</div>}
          {events.map((event, index) => (
            <div key={index} className={event.type}>
              <span className="ts">
                {Number.isFinite(event.ts)
                  ? new Date(event.ts * 1000).toLocaleTimeString("vi-VN", { hour12: false })
                  : "--:--:--"}
              </span>
              {event.stage && <b>[{event.stage}] </b>}
              {event.message}
            </div>
          ))}
        </div>
      </div>
      <ScrollToBottomPill
        visible={!pinned}
        count={pendingCount}
        onClick={() => scrollToBottom({ smooth: true })}
      />
    </div>
  );
};
