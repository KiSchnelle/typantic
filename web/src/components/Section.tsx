import { useState } from "react";
import type { ReactNode } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

// A collapsible form section with a clickable header.
export function Section({
  title,
  count,
  defaultOpen = true,
  children,
}: {
  title: string;
  count?: number;
  defaultOpen?: boolean;
  children: ReactNode;
}): ReactNode {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="section">
      <button
        type="button"
        className="section-head"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
      >
        {open ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
        <span className="section-title">{title}</span>
        {count !== undefined && <span className="section-count">{count}</span>}
      </button>
      {open && <div className="section-body">{children}</div>}
    </div>
  );
}
