import React from "react";

export type FlowState = "done" | "now" | "todo" | "wait";

export const FlowSteps: React.FC<{
  steps: { id: string; label: string; state: FlowState }[];
}> = ({ steps }) => (
  <ol className="flow" aria-label="Quy trình">
    {steps.map((step, index) => (
      <li key={step.id} className={`flow-step ${step.state}`}>
        <span className="flow-num" aria-hidden>
          {index + 1}
        </span>
        <span>{step.label}</span>
      </li>
    ))}
  </ol>
);
