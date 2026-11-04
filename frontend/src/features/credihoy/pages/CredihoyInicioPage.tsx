import icono from "../../../assets/IconoHD.png";

export function CredihoyInicioPage({ onStart, busy }: { onStart: () => void; busy: boolean }) {
  return <section className="ch-start">
    <div className="ch-start-brand"><img src={icono} alt="Crédito Digital Congente" /><span>Crédito Digital Congente</span></div>
    <p className="ch-eyebrow">Tu solicitud digital</p>
    <h1>CrediHoy</h1>
    <p className="ch-intro">Tu próxima solicitud de crédito, con la confianza de Congente.</p>
    <p className="ch-start-copy">Completa tu información, define el monto y el plazo, y revisa tu solicitud antes de enviarla.</p>
    <button type="button" className="btn btn-brand" onClick={onStart} disabled={busy}>
      {busy ? "Preparando solicitud..." : "Iniciar solicitud"}
    </button>
    <div className="ch-start-facts">
      <div><span className="ch-fact-number">01</span><strong>Solicitud digital</strong><p>Tus datos y tu solicitud en un solo lugar.</p></div>
      <div><span className="ch-fact-number">02</span><strong>Un proceso sencillo</strong><p>Revisa cada paso antes de continuar.</p></div>
      <div><span className="ch-fact-number">03</span><strong>Tu autorización primero</strong><p>Decides sobre el uso de tus datos.</p></div>
    </div>
    <div className="ch-note">¿Tienes un CrediHoy vigente? Puedes solicitar su recogida dentro de esta nueva solicitud, sujeta a validación.</div>
  </section>;
}
