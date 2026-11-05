"""DHWS3 v1.9 tables 16/27 over an already owned and integrity-checked SOAP."""


from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import re
from lxml import etree
from ..base import DTO, Hecho, EstadoCertificacion as EC


@dataclass(frozen=True, kw_only=True)
class EmbargosInforme(DTO):
    cuentas_embargadas: Hecho[bool]
    embargos_abiertos_alerta: Hecho[int]
    cuentas_embargadas_observadas: int
    cuentas_reactivadas_observadas: int
    cobertura_cuentas_completa: bool


def leer_embargos_persistidos(xml: bytes, *, referencia: str, fecha_consulta: datetime,
                             fecha_corte: date | None, cobertura: tuple[int, int]):
    if not isinstance(xml, bytes) or not 0<len(xml)<=16*1024*1024:
        raise ValueError('XML_HDC_INVALIDO')
    def parse(raw):
        root=etree.fromstring(raw,etree.XMLParser(resolve_entities=False,load_dtd=False,
            no_network=True,recover=False,huge_tree=False))
        if root.getroottree().docinfo.doctype:raise ValueError('DTD_HDC_PROHIBIDO')
        return root
    roots=[parse(xml)]
    for node in roots[0].iter():
        if node.text and node.text.lstrip().startswith('<'):
            roots.append(parse(node.text.encode('utf-8')))
    reports=[]
    for root in roots:
        reports.extend(root.xpath('//*[local-name()="Informes"]/*[local-name()="Informe"]'))
    digest=hashlib.sha256(xml).hexdigest()
    def fact(value,path,note):
        return Hecho(valor=value,disponible=value is not None,fuente='HDC_DHWS3_1.9',
            referencia_evidencia=referencia+':sha256:'+digest,ruta_origen=path,
            fecha_consulta=fecha_consulta,fecha_corte=fecha_corte,
            estado_certificacion=EC.CERTIFICADO if value is not None and fecha_corte is not None else
                EC.OBSERVADO if value is not None else EC.NO_CERTIFICADO,observacion=note)
    alerts_open=None;positive=False;unknown_alert=False;seized=reactivated=0;complete=False
    if len(reports)==1:
        report=reports[0]
        bank_counts=[];codes=[]
        for tag in ('CuentaAhorro','CuentaCorriente'):
            accounts=report.xpath('./*[local-name()=$name]',name=tag);bank_counts.append(len(accounts))
            for account in accounts:
                states=account.xpath('./*[local-name()="Estado"]/@codigo')
                codes.append(states[0] if len(states)==1 else None)
        complete=tuple(bank_counts)==cobertura and bool(codes) and all(c in {'01','02','05','06','07','09'} for c in codes)
        seized=codes.count('06');reactivated=codes.count('07');positive=seized>0
        counts=[]
        for alert in report.xpath('./*[local-name()="Alerta"]'):
            sources=alert.xpath('./*[local-name()="Fuente"]/@codigo')
            source=alert.get('fuente') or (sources[0] if len(sources)==1 else None)
            if alert.get('codigo')=='002' and source is None:
                unknown_alert=True
            if source!='000001' or alert.get('codigo')!='002':continue
            # Table 27 documents this exact text; arbitrary numbers are not counts.
            description=alert.get('descripcion') or ''.join(alert.itertext())
            match=re.fullmatch(r'\s*Tiene\s+(\d+)\s+embargos\s+cerrados\s+y\s+(\d+)\s+abiertos[.\s]*(?:El abierto m[a\u00e1]s reciente se coloc[o\u00f3] el\s+.+)?',description,re.IGNORECASE)
            if match:counts.append(int(match.group(2)))
            else:unknown_alert=True
        if counts and len(set(counts))==1:alerts_open=counts[0];positive=positive or alerts_open>0
        elif counts:unknown_alert=True
    value=True if positive else False if complete and not unknown_alert and alerts_open in (None,0) else None
    if alerts_open==0 and not complete:value=None
    return EmbargosInforme(cuentas_embargadas=fact(value,
        '/Informes/Informe/{CuentaAhorro,CuentaCorriente}/Estado/@codigo;Alerta[fuente=000001,codigo=002]',
        '06 vigente; 07 reactivada. Negativo solo con cobertura y estados completos, no por ausencia de alertas'),
        embargos_abiertos_alerta=fact(alerts_open,'/Informes/Informe/Alerta[fuente=000001,codigo=002]',
            'Conteo explicito de la alerta; no se suma con cuentas para evitar duplicidad'),
        cuentas_embargadas_observadas=seized,cuentas_reactivadas_observadas=reactivated,
        cobertura_cuentas_completa=complete)


def informe_hdc_concordante(xml, evidence):
    """Bind the stored XML to the normalized report before deriving new facts."""
    if not isinstance(xml, bytes) or not 0 < len(xml) <= 16 * 1024 * 1024:
        raise ValueError('XML_HDC_INVALIDO')

    def parse(raw):
        try:
            root = etree.fromstring(raw, etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True))
        except etree.XMLSyntaxError:
            raise ValueError('XML_HDC_INVALIDO') from None
        if root.getroottree().docinfo.doctype:
            raise ValueError('XML_DTD_PROHIBIDO')
        return root
    roots = [parse(xml)]
    for node in roots[0].iter():
        if node.text and node.text.lstrip().startswith('<'):
            roots.append(parse(node.text.encode()))
    reports = []
    for root in roots:
        reports.extend(root.xpath('//*[local-name()="Informes"]/*[local-name()="Informe"]'))
    expected = dict(evidence['resultado']['atributos_informe_originales'])
    if len(reports) != 1 or not expected or dict(reports[0].attrib) != expected:
        raise ValueError('XML_HDC_NO_CORRESPONDE_A_RESPUESTA')
    return etree.tostring(reports[0].getparent())
