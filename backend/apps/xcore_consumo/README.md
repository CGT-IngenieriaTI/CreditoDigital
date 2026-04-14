# xcore_consumo

Esta app contiene el flujo robusto del producto de credito digital de consumo.

Que hace:
- Orquesta el flujo completo: datos basicos -> documentos -> OTP -> PRESELECTA -> historial -> formulario -> evaluacion -> decision.
- Consulta Oracle/LINIX y estamentos.
- Consolida datos del formulario y del XML HC2.
- Calcula score, tasa, configuraciones, estamento y evaluacion de consumo.
- Guarda OTP, consentimiento, snapshots, errores e integraciones.
- Administra la configuracion cargada desde JSON (`regresion`, `gastos familiares`, `agencias`, `tasas`).

Modelos principales:
- `SolicitudConsumo`
- `OtpChallenge`
- `ConsentimientoConsumo`
- `ConsultaCoreOracle`
- `ConsultaEstamentoOracle`
- `EvaluacionConsumo`
- `ConsultaAsociadoIntento`
- `ConfiguracionRegresion`
- `ConfiguracionGastosFamiliares`
- `ConfiguracionAgenciaCanal`
- `TasaInteresConsumo`

Diferencia frente a `xcore`:
- Esta es la app activa y completa del flujo de consumo.
- `xcore` es una integracion mas simple; `xcore_consumo` es el producto completo con reglas de negocio.
