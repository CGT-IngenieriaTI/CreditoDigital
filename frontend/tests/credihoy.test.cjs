const { test, before } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path");
const ts = require("typescript"), Module = require("node:module");
const React = require("react"), { renderToStaticMarkup } = require("react-dom/server");
const root = path.resolve(__dirname, "../src/features/credihoy");
function modules(dev = true) {
  const cache = new Map();
  function load(name) {
    const candidate = path.resolve(root, /\.tsx?$/.test(name) ? name : name + ".ts");
    const filename = fs.existsSync(candidate) ? candidate : candidate + "x";
    if (cache.has(filename)) return cache.get(filename).exports;
    const source = fs.readFileSync(filename, "utf8").replaceAll("import.meta.env.DEV", String(dev));
    const code = ts.transpileModule(source, { fileName: filename, compilerOptions: {
      jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020
    } }).outputText;
    const m = new Module(filename); cache.set(filename, m);
    m.require = target => target.startsWith(".") ? load(path.relative(root, path.resolve(path.dirname(filename), target))) : require(target);
    m._compile(code, filename); return m.exports;
  }
  return load;
}
const load = modules(), v = load("validation"), s = load("state"), money = load("money"), local = load("services/local");
const signal = () => new AbortController().signal;
let contexto, condicionesProducto, consulta;
before(async () => {
  const adapter = local.crearServicioLocal();
  contexto = await adapter.cargarContextoSolicitud(signal());
  condicionesProducto = await adapter.cargarCondicionesProducto(signal());
  consulta = await adapter.consultarCredihoyVigente(local.DATOS_SINTETICOS, signal());
});
function validState() {
  let state = s.credihoyReducer(s.crearEstadoInicial(), { type: "CONTEXTO", contexto, condicionesProducto });
  for (const action of [
    { type: "DATOS", datos: { ...local.DATOS_SINTETICOS } },
    { type: "FINANZAS", finanzas: { ...local.FINANZAS_SINTETICAS } },
    { type: "OBLIGACIONES", consulta },
    { type: "SOLICITUD", solicitud: { monto: "530000.01", plazoMeses: "3" } }
  ]) state = s.credihoyReducer(state, action);
  for (const doc of contexto.documentos) state = s.credihoyReducer(state, { type: "CONSENTIMIENTO", id: doc.id, aceptado: true });
  return state;
}
test("blank draft has no fabricated facts or implicit pickup", () => {
  const st = s.crearEstadoInicial();
  assert.equal(st.estadoSolicitud, "BORRADOR"); assert.equal(st.datos.identificacion, "");
  assert.equal(st.finanzas.ingresos, ""); assert.equal(st.condiciones, null); assert.equal(st.solicitud.recogerCredihoy, false);
});
for (const value of ["0", "0.00", "9007199254740993.12"]) test("decimal accepts " + value, () => assert.equal(money.importeValido(value), true));
for (const value of ["", "-1", "1e4", "1.001", "1,000", "NaN"]) test("decimal rejects " + JSON.stringify(value), () => assert.equal(money.importeValido(value), false));
test("COP editing preserves exact decimals, comma, deletion and zero", () => {
  assert.equal(money.formatoEdicionCop("5000000"), "5.000.000");
  assert.equal(money.formatoEdicionCop("1250000.05"), "1.250.000,05");
  assert.equal(money.editarCop("$ 1.250.000,05"), "1250000.05");
  assert.equal(money.editarCop("5.000.000,"), "5000000.");
  assert.equal(money.editarCop(""), ""); assert.equal(money.editarCop("0"), "0");
  assert.equal(money.editarCop("NaN"), null); assert.equal(money.editarCop("1,001"), null);
  assert.equal(money.moneda("9007199254740993.12"), "$ 9.007.199.254.740.993,12");
});
test("unavailable money is never zero", () => {
  const { valorFinanciero } = load("components/FormControls");
  assert.match(valorFinanciero({ disponible: false, valor: null }), /Pendiente/);
  assert.match(valorFinanciero({ disponible: true, valor: "0" }), /0/);
});
test("declared zero differs from missing values", () => {
  assert.deepEqual(v.validarFinanzas({ ingresos: "0", otrosIngresos: "0", gastos: "0" }), {});
  assert.ok(v.validarFinanzas({ ingresos: "", otrosIngresos: "0", gastos: "0" }).ingresos);
  assert.ok(v.validarFinanzas({}).ingresos);
});
test("identity and contact formats are validated", () => {
  const datos = validState().datos; assert.deepEqual(v.validarDatos(datos), {});
  assert.ok(v.validarDatos({ ...datos, identificacion: "١٢" }).identificacion);
  const bad = v.validarDatos({ ...datos, nombres: " ", celular: "1", correo: "bad" });
  assert.ok(bad.nombres); assert.ok(bad.celular); assert.ok(bad.correo);
});
test("normalization trims without uppercasing", () => {
  const datos = v.normalizarDatos({ ...local.DATOS_SINTETICOS, nombres: " Persona Prueba ", correo: " TEST@example.invalid " });
  assert.equal(datos.nombres, "Persona Prueba"); assert.equal(datos.correo, "TEST@example.invalid");
});
test("term uses supplied conditions, never fallback limits", () => {
  const st = validState();
  for (const plazoMeses of ["1", "48"]) assert.deepEqual(v.validarSolicitud({ ...st.solicitud, plazoMeses }, condicionesProducto, consulta), {});
  for (const plazoMeses of ["", "0", "49", "1.5", "1e1"]) assert.ok(v.validarSolicitud({ ...st.solicitud, plazoMeses }, condicionesProducto, consulta).plazoMeses);
  assert.ok(v.validarSolicitud(st.solicitud, { ...condicionesProducto, plazoMaximoMeses: null }, consulta).plazoMeses);
  assert.ok(v.validarSolicitud(st.solicitud, null, consulta).plazoMeses);
  assert.deepEqual(v.validarSolicitud({ ...st.solicitud, plazoMeses: "60" }, { ...condicionesProducto, plazoMaximoMeses: 72 }, consulta), {});
});
test("API mode cannot use local fixture conditions and supports explicit pending bounds", () => {
  assert.equal(v.condicionesValidas(condicionesProducto), false);
  assert.equal(v.condicionesValidas(condicionesProducto, true), true);
  assert.equal(v.condicionesValidas({ ...condicionesProducto, estado: "PENDIENTE_BACKEND", plazoMinimoMeses: null, plazoMaximoMeses: null }), true);
  assert.equal(v.condicionesValidas({ ...condicionesProducto, estado: "INFORMADAS_POR_BACKEND", plazoMaximoMeses: 0 }), false);
});
test("amount uses exact configured boundaries, not SMMLV arithmetic", () => {
  const st = validState();
  assert.ok(v.validarSolicitud({ ...st.solicitud, monto: "0" }, condicionesProducto, consulta).monto);
  assert.ok(v.validarSolicitud({ ...st.solicitud, monto: "9007199254740993.12" }, { ...condicionesProducto, montoMaximo: "9007199254740993.11" }, consulta).monto);
});
test("backend eligibility and modality compatibility are both required", () => {
  const st = validState(), o = consulta.obligaciones[0];
  assert.equal(v.esCredihoyRecogible(o), true);
  assert.equal(v.esCredihoyRecogible({ ...o, cuotaMensual: { disponible: false, valor: null } }), true);
  for (const modalidadCodigo of ["112", "127", "121", "150"]) assert.equal(v.esCredihoyRecogible({ ...o, modalidadCodigo }), false);
  for (const producto of ["CUPO_ROTATIVO", "CONSUMO"]) assert.equal(v.esCredihoyRecogible({ ...o, producto }), false);
  assert.equal(v.esCredihoyRecogible({ ...o, persona: "JURIDICA" }), false);
  const solicitud = { ...st.solicitud, recogerCredihoy: true, obligacionRecogidaReferencia: o.referencia };
  assert.deepEqual(v.validarSolicitud(solicitud, condicionesProducto, consulta), {});
  for (const estado of ["SIN_CREDIHOY", "CREDIHOY_NO_ELEGIBLE", "PENDIENTE_CONSULTA"]) assert.ok(v.validarSolicitud(solicitud, condicionesProducto, { ...consulta, estado }).obligacionRecogidaReferencia);
  assert.equal(st.solicitud.recogerCredihoy, false);
});
test("pickup requires explicit selection; no hidden references", () => {
  const st = validState();
  assert.ok(v.validarSolicitud({ ...st.solicitud, recogerCredihoy: true }, condicionesProducto, consulta).obligacionRecogidaReferencia);
  assert.ok(v.validarSolicitud({ ...st.solicitud, obligacionRecogidaReferencia: "hidden" }, condicionesProducto, consulta).obligacionRecogidaReferencia);
});
test("editing invalidates stale evidence, conditions and acceptance", () => {
  const original = { ...validState(), condiciones: {}, seguimiento: {}, solicitud: { ...validState().solicitud, recogerCredihoy: true, obligacionRecogidaReferencia: "old" } };
  const st = s.credihoyReducer(original, { type: "DATOS", datos: { identificacion: "1" } });
  assert.equal(st.condiciones, null); assert.equal(st.consultaObligaciones, null); assert.equal(st.seguimiento, null);
  assert.equal(st.solicitud.recogerCredihoy, false); assert.deepEqual(st.consentimientos, {});
  assert.equal(s.credihoyReducer(original, { type: "FINANZAS", finanzas: { ingresos: "0" } }).condiciones, null);
});
test("navigation cannot skip steps, preview or receipt", () => {
  let st = { ...validState(), paso: "inicio" };
  assert.equal(s.credihoyReducer(st, { type: "PASO", paso: "resumen" }), st);
  for (const paso of ["datos", "financiero", "solicitud"]) { st = s.credihoyReducer(st, { type: "PASO", paso }); assert.equal(st.paso, paso); }
  assert.equal(s.credihoyReducer(st, { type: "PASO", paso: "resumen" }), st);
  st = s.credihoyReducer(st, { type: "CONDICIONES", condiciones: { cuota: { disponible: false, valor: null }, capacidad: { disponible: false, valor: null } } });
  st = s.credihoyReducer(st, { type: "PASO", paso: "resumen" }); assert.equal(st.paso, "resumen");
  assert.equal(s.credihoyReducer(st, { type: "PASO", paso: "estado" }), st);
  assert.equal(s.pasoAnterior("inicio"), "inicio");
});
test("dynamic required consents enforced; optional acceptance stays optional", () => {
  const docs = [{ ...contexto.documentos[0], id: "nuevo-requerido" }, { ...contexto.documentos[1], id: "opcional", requerido: false }];
  let st = { ...validState(), contexto: { documentos: docs }, consentimientos: { "nuevo-requerido": true } };
  assert.equal(s.autorizacionesCompletas(st), true);
  assert.deepEqual(s.construirEnvio(st).consentimientos.map(c => c.aceptado), [true, false]);
  st = { ...st, consentimientos: { "nuevo-requerido": false } };
  assert.equal(s.autorizacionesCompletas(st), false); assert.throws(() => s.construirEnvio(st));
});
test("preview never manufactures authorizations", () => {
  const st = { ...validState(), consentimientos: {} };
  assert.equal(s.construirEnvio(st, false).consentimientos.every(c => !c.aceptado), true);
  assert.throws(() => s.construirEnvio(st));
});
test("missing, duplicate, unreadable and unsafe documents fail closed", () => {
  assert.equal(s.documentosValidos({ documentos: [] }), false);
  assert.equal(s.documentosValidos({ documentos: [contexto.documentos[0], contexto.documentos[0]] }), false);
  assert.equal(s.documentosValidos({ documentos: [{ ...contexto.documentos[0], contenido: null, url: "https://third-party.invalid" }] }), false);
  assert.equal(s.urlDocumentoSegura("/documentos/ejemplo"), "/documentos/ejemplo");
  for (const url of ["//third-party.invalid", "/\\bad", "javascript:alert(1)", "/a b"]) assert.equal(s.urlDocumentoSegura(url), null);
});
test("payload preserves amount and declared data, without calculations", () => {
  const p = s.construirEnvio(validState());
  assert.equal(p.producto, "CREDIHOY UNICO (NUEVO)"); assert.equal(p.solicitud.monto, "530000.01"); assert.equal(p.solicitud.plazoMeses, 3);
  assert.equal(p.capacidad, undefined); assert.equal(p.garantia, undefined);
});
test("new request fully clears entered data, acceptance and tracking", () => {
  const reset = s.credihoyReducer({ ...validState(), paso: "estado", seguimiento: { estado: "RECIBIDA" } }, { type: "REINICIAR" });
  const blank = s.crearEstadoInicial();
  for (const key of ["datos", "finanzas", "solicitud", "seguimiento", "consultaObligaciones", "condiciones", "consentimientos", "estadoSolicitud"]) assert.deepEqual(reset[key], blank[key]);
  assert.equal(reset.paso, "inicio"); assert.equal(reset.contexto, contexto);
});
test("request state follows backend; resume allowed only for its draft", () => {
  const st = s.credihoyReducer(validState(), { type: "ENVIADA", seguimiento: { estado: "RECIBIDA" } });
  assert.equal(st.estadoSolicitud, "RECIBIDA"); assert.equal(st.paso, "estado");
  assert.equal(s.credihoyReducer(st, { type: "RETOMAR" }), st);
  assert.equal(s.credihoyReducer(s.credihoyReducer(st, { type: "SEGUIMIENTO", seguimiento: { estado: "BORRADOR" } }), { type: "RETOMAR" }).paso, "datos");
});
for (const escenario of ["SIN_CREDIHOY", "CREDIHOY_ELEGIBLE", "CREDIHOY_NO_ELEGIBLE", "PENDIENTE_CONSULTA"]) {
  test("local adapter preserves " + escenario, async () => {
    const result = await local.crearServicioLocal({ escenario }).consultarCredihoyVigente(local.DATOS_SINTETICOS, signal());
    assert.equal(result.estado, escenario); assert.equal(v.obligacionesElegibles(result).length, escenario === "CREDIHOY_ELEGIBLE" ? 1 : 0);
  });
}
test("local preview and receipt never calculate or approve", async () => {
  const adapter = local.crearServicioLocal(), payload = s.construirEnvio(validState());
  const preview = await adapter.previsualizarSolicitud(payload, signal());
  assert.deepEqual(preview.cuota, { disponible: false, valor: null }); assert.deepEqual(preview.capacidad, { disponible: false, valor: null });
  const result = await adapter.enviarSolicitud(payload, "id-prueba", signal());
  assert.equal(result.estado, "RECIBIDA"); assert.equal(result.autorizadoDecisionCrediticia, false); assert.equal(result.sintetico, true);
  assert.equal((await adapter.consultarEstado(result.referencia, signal())).estado, "RECIBIDA");
});
test("adapter supports typed retries and cancellation", async () => {
  await assert.rejects(local.crearServicioLocal({ fallo: "CONSULTA" }).consultarCredihoyVigente(local.DATOS_SINTETICOS, signal()), e => e.tipo === "retryable_error");
  const controller = new AbortController(), operation = local.crearServicioLocal().cargarContextoSolicitud(controller.signal);
  controller.abort(); await assert.rejects(operation, { name: "AbortError" });
});
test("local adapter is unavailable in production", () => assert.throws(() => modules(false)("services/local").crearServicioLocal()));
for (const estado of load("types").ESTADOS_SOLICITUD) test("request UI represents " + estado, () => {
  const { CredihoyEstadoPage, PRESENTACION_ESTADOS } = load("pages/CredihoyEstadoPage");
  const html = renderToStaticMarkup(React.createElement(CredihoyEstadoPage, {
    seguimiento: { referencia: "REF-PRUEBA", estado, fecha: "", mensaje: "Detalle de prueba", sintetico: false, autorizadoDecisionCrediticia: false },
    solicitud: validState().solicitud, onReset() {}, onUpdate() {}, onResume() {}
  }));
  assert.ok(html.includes(PRESENTACION_ESTADOS[estado].titulo)); assert.ok(html.includes(PRESENTACION_ESTADOS[estado].cta));
});
test("production presentation never leaks synthetic labels or reference", () => {
  const { CredihoyEstadoPage } = modules(false)("pages/CredihoyEstadoPage");
  const html = renderToStaticMarkup(React.createElement(CredihoyEstadoPage, {
    seguimiento: { referencia: "LOCAL-SOLICITUD-FICTICIA", estado: "RECIBIDA", fecha: "", mensaje: "Recorrido local completado", sintetico: true, autorizadoDecisionCrediticia: false },
    solicitud: validState().solicitud, local: true, onReset() {}
  }));
  assert.doesNotMatch(html, /LOCAL-|sintético|Recorrido local/);
});
for (const estado of ["idle", "loading", "success", "empty", "validation_error", "technical_error", "retryable_error"]) test("integration UI supports " + estado, () => {
  const { IntegrationState } = load("components/IntegrationState");
  const html = renderToStaticMarkup(React.createElement(IntegrationState, { integracion: { estado }, onRetry() {} }));
  if (estado === "retryable_error") assert.ok(html.includes("Reintentar"));
  if (estado.endsWith("error")) assert.ok(html.includes('role="alert"'));
  if (estado === "loading") assert.ok(html.includes('role="status"'));
});
test("actual entry keeps legacy for missing/off flags and other routes", () => {
  const filename = path.resolve(root, "../../RootExperience.tsx"), LegacyContext = () => null, old = global.window;
  try {
    for (const [flag, pathname, expected] of [[undefined, "/credihoy", false], ["0", "/credihoy", false], ["true", "/credihoy", false], ["1", "/", false], ["1", "/credihoy", true], ["1", "/credihoy/", true]]) {
      global.window = { location: { pathname } };
      const source = fs.readFileSync(filename, "utf8").replaceAll("import.meta.env.VITE_CREDIHOY_UNICO_ENABLED", flag === undefined ? "undefined" : JSON.stringify(flag));
      const code = ts.transpileModule(source, { compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
      const m = new Module(filename); m.require = target => target === "./context/CreditFlowContext" ? { CreditFlowProvider: LegacyContext } : require(target);
      m._compile(code, filename); assert.equal(m.exports.default().props.children.type !== LegacyContext, expected);
    }
  } finally { global.window = old; }
});
test("module network restricted to explicit HTTP adapters; persistence only for expiring technical attempt; flag default off", () => {
  const adapters=new Set(['shadow.ts','canal.ts','consentimientoReal.ts','publico.ts','tasas.ts']
    .map(name=>path.join('services',name)));
  function storageGuard(source,file){
    assert.doesNotMatch(source,/\b(?:axios|creditApi|localStorage|indexedDB)\b/);
    if(!/\bsessionStorage\b/.test(source))return;
    if(path.relative(root,file)===path.join('asociado','PortalAsociado.tsx')){
      const ast=ts.createSourceFile(file,source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
      assert.match(source,/const storage='credihoy_contractual_acto:'\+solicitud\.referencia/);
      function visit(node,scope=''){
        if(ts.isFunctionDeclaration(node))scope=node.name?.text??'';
        if(ts.isIdentifier(node)&&node.text==='sessionStorage'){
          assert.ok(['FirmaContractualStaging','revisarNuevamente'].includes(scope));
          const member=node.parent,call=member.parent;
          assert.ok(ts.isPropertyAccessExpression(member)&&ts.isCallExpression(call));
          assert.ok(['getItem','setItem'].includes(member.name.text));
          assert.ok(['storage',"'credihoy_contractual_acto:'+solicitud.referencia"].includes(call.arguments[0].getText(ast)));
          if(member.name.text==='setItem'){
            const name=call.arguments[1].getText(ast);assert.ok(['id','next'].includes(name));
            assert.match(source,new RegExp('const '+name+'=crypto\\.randomUUID\\(\\)'));
          }
        }
        ts.forEachChild(node,child=>visit(child,scope));
      }
      visit(ast);return;
    }
    assert.equal(path.relative(root,file),path.join('components','CredihoyAccesoAsociado.tsx'));
    const ast=ts.createSourceFile(file,source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
    function visit(node,scope=''){
      if(ts.isFunctionDeclaration(node))scope=node.name?.text??'';
      if(ts.isIdentifier(node)&&node.text==='sessionStorage'){
        assert.ok(['leerIntento','guardarIntento'].includes(scope),'Only technical attempt helpers may persist');
        const member=node.parent,call=member.parent;
        assert.ok(ts.isPropertyAccessExpression(member)&&ts.isCallExpression(call));
        assert.ok(['getItem','setItem','removeItem'].includes(member.name.text));
        assert.equal(call.arguments[0].getText(ast),'DRAFT');
        if(member.name.text==='setItem'){
          const payload=call.arguments[1];assert.ok(ts.isCallExpression(payload));
          assert.equal(payload.expression.getText(ast),'JSON.stringify');
          const data=payload.arguments[0];assert.ok(ts.isObjectLiteralExpression(data));
          assert.deepEqual(data.properties.map(p=>p.name.getText(ast)),['id','venceAt']);
          assert.equal(data.properties[0].getText(ast),'id');
          assert.match(data.properties[1].getText(ast),/^venceAt:\s*Date\.now\(\)\+ttl\*1000$/);
        }
      }
      ts.forEachChild(node,child=>visit(child,scope));
    }
    visit(ast);
    assert.match(source,/const DRAFT='credihoy_publico_intento_v1'/);
    assert.match(source,/p\.venceAt>Date\.now\(\)/);
    assert.match(source,/p\.venceAt<=Date\.now\(\)\+300000/);
  }
  function inspect(dir) {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const file = path.join(dir, entry.name);
      if (entry.isDirectory()) inspect(file);
      else if (/\.tsx?$/.test(file)) {
        const source = fs.readFileSync(file, "utf8");
        storageGuard(source,file);
        if (!adapters.has(path.relative(root,file))) assert.doesNotMatch(source, /\bfetch\b/);
        assert.doesNotMatch(source, /(?:from|import\s*\()\s*["'][^"']*(?:\/api\/|\/context\/CreditFlow|historial_pago|preselecta)/i);
      }
    }
  }
  inspect(root); assert.match(fs.readFileSync(path.resolve(root, "../../../.env.example"), "utf8"), /VITE_CREDIHOY_UNICO_ENABLED=0/);
  const accessFile=path.join(root,'components','CredihoyAccesoAsociado.tsx');
  const access=fs.readFileSync(accessFile,'utf8');
  for(const wrong of [access.replace('{id,venceAt:', '{id,codigo,venceAt:'),
    access.replace('function guardarIntento','function guardarCredenciales'),
    access.replace('sessionStorage.setItem(DRAFT','sessionStorage.setItem("personal"')]){
    assert.throws(()=>storageGuard(wrong,accessFile),assert.AssertionError);
  }
  const portalFile=path.join(root,'asociado','PortalAsociado.tsx'),portal=fs.readFileSync(portalFile,'utf8');
  for(const wrong of [portal.replace('sessionStorage.setItem(storage,id)','sessionStorage.setItem(storage,codigo)'),
    portal.replace("const storage='credihoy_contractual_acto:'","const storage='personal:'")]){
    assert.throws(()=>storageGuard(wrong,portalFile),assert.AssertionError);
  }
});
test("module imports stay isolated from backend, providers and legacy frontend", () => {
  const assets = new Set(["LogoHD.png", "IconoHD.png"].map(name => path.resolve(root, "../../assets", name)));
  const sharedViewer=path.resolve(root,'../../components/PdfScrollViewer');
  function checkImports(source, filename) {
    const ast = ts.createSourceFile(filename, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
    function check(specifier) {
      assert.ok(specifier && ts.isStringLiteralLike(specifier), "Module imports must be literal");
      const name = specifier.text;
      if (!name.startsWith(".")) return assert.ok(['react','lucide-react'].includes(name), `Forbidden dependency: ${name}`);
      const target = path.resolve(path.dirname(filename), name), relative = path.relative(root, target);
      assert.ok(assets.has(target) || target===sharedViewer || (!path.isAbsolute(relative) && relative !== ".." && !relative.startsWith(".." + path.sep)),
        `Import escapes CrediHoy module: ${name}`);
    }
    function visit(node) {
      if (ts.isImportDeclaration(node)) check(node.moduleSpecifier);
      else if (ts.isExportDeclaration(node) && node.moduleSpecifier) check(node.moduleSpecifier);
      else if (ts.isImportEqualsDeclaration(node) && ts.isExternalModuleReference(node.moduleReference)) check(node.moduleReference.expression);
      else if (ts.isImportTypeNode(node)) check(ts.isLiteralTypeNode(node.argument) ? node.argument.literal : undefined);
      else if (ts.isCallExpression(node) && (node.expression.kind === ts.SyntaxKind.ImportKeyword
        || (ts.isIdentifier(node.expression) && node.expression.text === "require"))) check(node.arguments[0]);
      ts.forEachChild(node, visit);
    }
    visit(ast);
  }
  function inspect(dir) {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const file = path.join(dir, entry.name);
      if (entry.isDirectory()) inspect(file);
      else if (/\.tsx?$/.test(file)) checkImports(fs.readFileSync(file, "utf8"), file);
    }
  }
  inspect(root);
  // Verify the guard itself without reading backend or legacy files.
  const filename = path.join(root, "boundary-test.tsx");
  for (const source of [
    'import "../../api/creditApi";',
    'export * from "../../pages/ApplicationFormPage";',
    'import type { State } from "../../context/CreditFlowContext";',
    'import Legacy = require("../../App");',
    'type Hdc = import("../../../../backend/apps/historial_pago/client").Hdc;',
    'import("risk-gateway");',
    'require("api_data_v1");',
    'import(provider);',
    'import "axios";'
  ]) assert.throws(() => checkImports(source, filename), assert.AssertionError, source);
  assert.doesNotThrow(() => checkImports('import "react"; import {ShieldCheck} from "lucide-react"; export * from "./types";', filename));
});
