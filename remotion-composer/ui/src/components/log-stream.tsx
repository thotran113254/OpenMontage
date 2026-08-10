import React, { useEffect, useRef } from "react";
import { ProgressEvent } from "../api/client";

export const LogStream: React.FC<{ events: ProgressEvent[] }> = ({ events }) => {
  const box = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);

  // stay pinned to the newest line unless the user scrolled up to read
  useEffect(() => {
    const element = box.current;
    if (element && pinned.current) element.scrollTop = element.scrollHeight;
  }, [events.length]);

  const onScroll = () => {
    const element = box.current;
    if (!element) return;
    pinned.current = element.scrollHeight - element.scrollTop - element.clientHeight < 40;
  };

  return (
    <div className="log" ref={box} onScroll={onScroll}>
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
  );
};
