interface StatusModalProps {
  visible: boolean;
  title: string;
  message: string;
  tone?: "success" | "info" | "warning";
  label?: string;
  details?: string[];
}

function StatusIcon({ tone }: { tone: NonNullable<StatusModalProps["tone"]> }) {
  if (tone === "warning") {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true" className="status-modal__icon-svg">
        <path d="M12 3.8 21 19.2H3L12 3.8Z" />
        <path d="M12 9.2v4.8" />
        <path d="M12 17.2h.01" />
      </svg>
    );
  }

  if (tone === "info") {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true" className="status-modal__icon-svg">
        <path d="M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Z" />
        <path d="M12 10.2v5.1" />
        <path d="M12 7.4h.01" />
      </svg>
    );
  }

  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" className="status-modal__icon-svg">
      <path d="M20 7 10.2 17 6 12.8" />
    </svg>
  );
}

export function StatusModal({
  visible,
  title,
  message,
  tone = "success",
  label,
  details = [],
}: StatusModalProps) {
  if (!visible) {
    return null;
  }

  return (
    <div className="status-modal" role="dialog" aria-modal="true" aria-label={title}>
      <div className={`status-modal__card status-modal__card--${tone}`}>
        <div className={`status-modal__icon status-modal__icon--${tone}`}>
          <StatusIcon tone={tone} />
        </div>
        {label ? <span className="status-modal__label">{label}</span> : null}
        <h3>{title}</h3>
        <p>{message}</p>
        {details.length > 0 ? (
          <div className="status-modal__details">
            {details.map((detail) => (
              <span key={detail}>{detail}</span>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}
