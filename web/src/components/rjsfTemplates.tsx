import { useRef, useState } from "react";
import type { ReactNode } from "react";
import { createPortal } from "react-dom";
import type {
  FieldTemplateProps,
  IconButtonProps,
  ObjectFieldTemplateProps,
  WidgetProps,
} from "@rjsf/utils";
import { ChevronDown, ChevronUp, HelpCircle, Plus, X } from "lucide-react";
import { Section } from "./Section.tsx";

// A "?" that reveals the field's help on hover/focus. The bubble is portaled to
// <body> and fixed-positioned from the icon's screen rect, so no ancestor's
// overflow (e.g. a collapsible section) can ever clip it.
export function HelpTip({ text }: { text: string }): ReactNode {
  const ref = useRef<HTMLSpanElement>(null);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
  const show = () => {
    const rect = ref.current?.getBoundingClientRect();
    if (!rect) return;
    const left = Math.max(8, Math.min(rect.left, window.innerWidth - 340));
    setPos({ top: rect.top - 8, left });
  };
  const hide = () => setPos(null);
  return (
    <span
      ref={ref}
      className="help-tip"
      tabIndex={0}
      aria-label={text}
      onMouseEnter={show}
      onMouseLeave={hide}
      onFocus={show}
      onBlur={hide}
    >
      <HelpCircle size={13} />
      {pos !== null &&
        createPortal(
          <span
            className="help-bubble"
            style={{ top: pos.top, left: pos.left }}
          >
            {text}
          </span>,
          document.body,
        )}
    </span>
  );
}

// The tooltip text: the field description plus its default value when the schema
// carries one (so "auto-determined if None" from the description and the concrete
// default both live in the hint).
export function fieldHint(schema: {
  description?: unknown;
  default?: unknown;
}): string {
  const description =
    typeof schema.description === "string" ? schema.description : "";
  const def = schema.default;
  const defText =
    def !== undefined && def !== null && def !== ""
      ? `Default: ${typeof def === "string" ? def : JSON.stringify(def)}`
      : "";
  return [description, defText].filter(Boolean).join("\n\n");
}

// Custom FieldTemplate: label + (optional) help tooltip, then the input — but NOT
// the inline description block RJSF renders by default (that is what made the big
// forms feel chaotic). The description moves into the tooltip.
export function FieldTemplate(props: FieldTemplateProps): ReactNode {
  const { id, classNames, style, label, required, children, errors, help, displayLabel, schema } =
    props;
  const hint = fieldHint(schema);
  return (
    <div className={classNames} style={style}>
      {displayLabel && label ? (
        <span className="rjsf-label-row">
          <label htmlFor={id}>
            {label}
            {required ? <span className="req">*</span> : null}
          </label>
          {hint ? <HelpTip text={hint} /> : null}
        </span>
      ) : null}
      {children}
      {errors}
      {help}
    </div>
  );
}

// Boolean fields render as a checkbox whose label RJSF draws itself (so the
// FieldTemplate above skips the label row). Give them the same "?" tooltip
// instead of the default inline description above the checkbox.
export function CheckboxWidget(props: WidgetProps): ReactNode {
  const { id, value, onChange, disabled, readonly, label, schema } = props;
  const hint = fieldHint(schema);
  return (
    <span className="rjsf-check-row">
      <input
        id={id}
        type="checkbox"
        checked={value === true}
        disabled={disabled || readonly}
        onChange={(e) => onChange(e.target.checked)}
      />
      <label htmlFor={id} className="rjsf-check-label">
        {label}
      </label>
      {hint ? <HelpTip text={hint} /> : null}
    </span>
  );
}

// RJSF's default array buttons are unstyled icon buttons that render as a bare
// circle (the Bootstrap glyph font isn't loaded). Replace them with clear ones.
function AddButton({ onClick, disabled }: IconButtonProps): ReactNode {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="rjsf-add-btn"
    >
      <Plus size={14} /> Add
    </button>
  );
}

function RemoveButton({ onClick, disabled }: IconButtonProps): ReactNode {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label="Remove"
      className="rjsf-icon-btn rjsf-remove-btn"
    >
      <X size={14} />
    </button>
  );
}

function MoveUpButton({ onClick, disabled }: IconButtonProps): ReactNode {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label="Move up"
      className="rjsf-icon-btn"
    >
      <ChevronUp size={14} />
    </button>
  );
}

function MoveDownButton({ onClick, disabled }: IconButtonProps): ReactNode {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label="Move down"
      className="rjsf-icon-btn"
    >
      <ChevronDown size={14} />
    </button>
  );
}

// Copy adds clutter for these simple scalar lists; hide it.
function CopyButton(): ReactNode {
  return null;
}

// Group the root form's fields into collapsible Required / Optional sections so
// a big command (detect) isn't one long wall. Optional starts collapsed — that
// is the clutter-tamer. Nested objects keep the default flat rendering.
export function ObjectFieldTemplate(props: ObjectFieldTemplateProps): ReactNode {
  const { properties, idSchema, schema, title } = props;
  if (idSchema.$id !== "root") {
    return (
      <fieldset>
        {title ? <legend>{title}</legend> : null}
        {properties.map((p) => (
          <div key={p.name}>{p.content}</div>
        ))}
      </fieldset>
    );
  }
  const required = new Set(schema.required ?? []);
  const requiredProps = properties.filter((p) => required.has(p.name));
  const optionalProps = properties.filter((p) => !required.has(p.name));
  return (
    <>
      {requiredProps.length > 0 && (
        <Section title="Required" count={requiredProps.length}>
          {requiredProps.map((p) => (
            <div key={p.name}>{p.content}</div>
          ))}
        </Section>
      )}
      {optionalProps.length > 0 && (
        <Section
          title="Optional"
          count={optionalProps.length}
          defaultOpen={requiredProps.length === 0}
        >
          {optionalProps.map((p) => (
            <div key={p.name}>{p.content}</div>
          ))}
        </Section>
      )}
    </>
  );
}

export const templates = {
  FieldTemplate,
  ObjectFieldTemplate,
  ButtonTemplates: {
    AddButton,
    RemoveButton,
    MoveUpButton,
    MoveDownButton,
    CopyButton,
  },
};
