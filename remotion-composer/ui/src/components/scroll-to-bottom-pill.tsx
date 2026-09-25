import React from "react";

/** Floating "↓ N mới" pill for a stick-to-bottom pane — see use-stick-to-bottom.ts. */
export const ScrollToBottomPill: React.FC<{
  visible: boolean;
  count: number;
  onClick: () => void;
}> = ({ visible, count, onClick }) => {
  if (!visible) return null;
  return (
    <button type="button" className="scroll-bottom-pill" onClick={onClick}>
      ↓ {count > 0 ? `${count} mới` : "Mới nhất"}
    </button>
  );
};
