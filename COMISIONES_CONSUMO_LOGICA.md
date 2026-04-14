# Lógica de Comisiones Consumo

## Ubicación exacta

- Lógica principal de cálculo:
  - `C:\.vscode\Project_Score_last\configuraciones\views.py`
  - Función: `_calcular_comision_garantia(...)`

- Tablas de tasas FNG embebidas en código:
  - `C:\.vscode\Project_Score_last\configuraciones\views.py`
  - Constantes:
    - `_FNG_EMP319_RAW`
    - `_FNG_EMP285_RAW`
    - `FNG_EMP319_RATES`
    - `FNG_EMP285_RATES`

- Punto donde se ejecuta el cálculo:
  - `C:\.vscode\Project_Score_last\configuraciones\views.py`
  - Dentro del flujo `recibir_data(...)`

- Punto donde se guarda en base de datos:
  - `C:\.vscode\Project_Score_last\configuraciones\views.py`
  - Función: `guardar_registro(...)`

- Punto donde se expone en la respuesta final:
  - `C:\.vscode\Project_Score_last\configuraciones\views.py`


## Salida que genera

La función retorna:

- `comision_tipo`
- `comision_tasa_base`
- `comision_tasa_total`
- `comision_valor`
- `comision_iva_valor`
- `comision_total_valor`

Fórmula común:

- `valor_base = monto * (tasa_base / 100)`
- `iva = valor_base * 0.19`
- `total = valor_base + iva`


## Reglas FGA en Consumo

Depende de:

- `tipo_cliente`
- `forma_pago`
- `perfil_riesgo`

### Asociado Antiguo

- Categoría A/B + Ventanilla = `4% + IVA`
- Categoría A/B + Nómina = `2% + IVA`
- Categoría C/D + Ventanilla = `6% + IVA`
- Categoría C/D + Nómina = `3% + IVA`

### Asociado Nuevo

- Categoría A + Ventanilla = `5% + IVA`
- Categoría A + Nómina = `2% + IVA`
- Categoría B + Ventanilla = `6% + IVA`
- Categoría B + Nómina = `2% + IVA`
- Categoría C/D + Ventanilla = `8% + IVA`
- Categoría C/D + Nómina = `3.5% + IVA`


## Reglas FNG en Consumo

### FNG EMP319

- Plazo permitido: `12 a 60 meses`
- Monto permitido: `1 a 6 SMMLV`
- La tasa sale de una tabla fija por plazo

### FNG EMP285

- Plazo permitido: `12 a 120 meses`
- Monto permitido: `1 a 36 SMMLV`
- Aplica para líneas de Consumo y Microcrédito
- La tasa sale de una tabla fija por plazo


## Nota importante

La lógica de Consumo hoy está hardcodeada en código Python, no parametrizada en base de datos.
