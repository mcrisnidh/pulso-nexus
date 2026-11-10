# PULSO NEXUS — R3 · Expediente de preaprobación de 84 identidades

**Corte:** 10 de noviembre de 2026 · **Estado:** `PREAPROBACION_NO_GO` · **Uso:** evidencia comercial; no importar como reglas operativas.
**Entorno objetivo:** worktree aislado `C:\Users\ucbaa\pulso-nexus-r3`, rama `feature/r3-controlled`, baseline FIX6.2 `0cb87e75726ba30b57e2e229347758d18324f8e7`.
**Archivo recibido:** `R3_clientes_con_nombre_nov(1).csv` · SHA256 `34c8a2097fed5d8a9d2c0ae5aeb003dd34d73ba1f3dbe12f968a8c24fc0550e7` (coincide con dictamen histórico).
**Integridad comprobada:** 84 claves distintas de exactamente 13 caracteres; 3,147 líneas históricas; 84/84 `classification=PENDING_REVIEW` y `approved=NO`; 84/84 registros con `name_variants=1`.

> Esta fuente contiene nombres comerciales truncados, frecuentemente a 25 caracteres. `lines` significa líneas del extracto OE965 del 1-nov-2026, **no piezas ni ventas**. No permite calcular impacto en cantidades o clasificar demanda actual de SSRS. El TXT se usa solo como evidencia histórica R3; no reaparece como fuente operativa de Refresh.

## 1. Hallazgos verificables y discrepancia a resolver

| Grupo de triaje, no de aprobación | Claves | Líneas históricas | Siguiente decisión |
|---|---:|---:|---|
| CAT aparente — serie 170… | 19 | 1,810 | Confirmar con autoridad comercial si esta identidad exacta se sustituye por Portal CAT; anotar evidencia/contrato. |
| CAT aparente adicional fuera serie 170… | 1 | 2 | Resolver explicitamente si esta identidad exacta entra a sustitucion Portal CAT y por que no estuvo entre las 19 historicas. |
| Excepciones CAT/Perkins | 4 | 49 | Validar propietario real, identidad exacta, alcance de Portal CAT y decision conservar/excluir; aportar evidencia. |
| TUPY, preservación obligatoria | 1 | 29 | Registrar excepcion explicitamente y comprobar que R3 nunca la retira; identificar cuenta exacta. |
| APTIV, identidad independiente | 1 | 2 | Corroborar cuenta exacta y cartera CSR Carla; no inventar DELPHI ni fusionarlas. |
| PECL, nombre por contrastar | 1 | 3 | Conciliar identidad exacta mediante maestro corporativo y documentar si hay alias; no aplicar prefijo PERKINS para excluir. |
| Resto sin indicio CAT en nombre | 57 | 1,252 | Obtener confirmacion de autoridad comercial para cada clave o acta firmada que anexe la lista exacta. |
| **TOTAL** | **84** | **3,147** | **Cero aprobaciones de identidad** |

**Desfase 19 frente a 20:** el dictamen preparatorio anotó 19 candidatas CAT por nombre. El CSV contiene **19 claves de la serie `170…`** con nombres Caterpillar/Catepillar y **una más**, `98753462  001` — `CATERPILLAR INC` (2 líneas). Son **20 indicios**, no 20 cuentas aprobadas. Resolver si la vigésima pertenece a Portal CAT o si existe una exclusión comercial documentada. La ortografía `CATEPILLAR` aparece en tres nombres y no debe ignorarse por una búsqueda literal defectuosa.

**Especiales CAT/Perkins:** `CATPERK   000`, `CATPERK   001`, `PERK1     001`, `PERK2     001` suman 49 líneas; sus claves y nombres no bastan para inferir la clasificación. `PERK1` contiene «Caterpillar Reman service» pero sigue siendo caso especial, no CAT aprobado. `PERK2` contiene «Cannon Group Ldf», y no debe clasificarse por el prefijo PERK.

**Otros controles obligatorios:** TUPY (`TUPY      000`, 29 líneas) **se conserva siempre**, por regla ya confirmada; APTIV (`240625    001`, 2 líneas) y DELPHI **son independientes**; DELPHI no figura de forma explícita en este corte, lo que no permite inventar ni aprobar una cuenta DELPHI. `PECL      001` muestra «CANNON PACKAGING & LOGIST» (3 líneas): verificar la identidad y sus posibles alias con maestro corporativo, especialmente frente al antecedente de nombre diferente observado en el proceso CFDI, sin asumir que ambos registros son el mismo cliente comercial.

## 2. Matriz completa de identidad exacta (solo triaje)

Cada entrada permanece en `PENDING_REVIEW`; `approved=NO`. Una propuesta de trabajo no autoriza eliminar/retener líneas por sí misma. Las claves dentro de backticks preservan los espacios de relleno.

### CAT aparente — serie 170… (19 claves / 1,810 líneas)

| `customer_key_exact` | Nombre literal del archivo | Líneas | Prioridad |
|---|---|---:|---|
| `1705483   001` | Caterpillar Industrias Mé | 9 | P1 |
| `1705488   001` | CATERPILLAR AMERICAS MEXI | 336 | P1 |
| `170577    001` | Caterpillar, Inc. | 83 | P1 |
| `170577    004` | Caterpillar, Inc. 68-00 | 226 | P1 |
| `170577    029` | Caterpillar Logistics 92- | 114 | P1 |
| `170577    037` | Caterpillar, Inc. 47-03 | 21 | P1 |
| `170577    183` | CATEPILLAR LOGISTICS SERV | 273 | P1 |
| `17057700  012` | CATEPILLAR 92-4S | 10 | P1 |
| `17057700  013` | CATERPILLAR LOGISTICS 92 | 6 | P1 |
| `17057700  021` | CATERPILLAR, LOGISTICS DT | 21 | P1 |
| `17057700  036` | CATERPILLAR INC 40-59 | 26 | P1 |
| `17057700  038` | CATERPILLAR INC 40-17 | 148 | P1 |
| `17057703  001` | CATERPILLAR INC 88-52 | 2 | P1 |
| `17057704  205` | CATERPILLAR REMANUFACTURI | 15 | P1 |
| `17057705  001` | CATERPILLAR LOGISTIC INC | 82 | P1 |
| `17057706  242` | CATERPILLAR TIANJIN LIMIT | 138 | P1 |
| `1705771   000` | Caterpillar China Mach Co | 1 | P1 |
| `17057791  000` | CATEPILLAR INC WACO INBOU | 195 | P1 |
| `17057799  001` | CATERPILLAR INDIA PVT. LT | 104 | P1 |

### CAT aparente adicional fuera serie 170… (1 claves / 2 líneas)

| `customer_key_exact` | Nombre literal del archivo | Líneas | Prioridad |
|---|---|---:|---|
| `98753462  001` | CATERPILLAR INC | 2 | P0 |

### Excepciones CAT/Perkins (4 claves / 49 líneas)

| `customer_key_exact` | Nombre literal del archivo | Líneas | Prioridad |
|---|---|---:|---|
| `CATPERK   000` | PERKINS INDIA PRIVATE LIM | 43 | P0 |
| `CATPERK   001` | PERKINS INDIA PRIVATE LIM | 2 | P0 |
| `PERK1     001` | Caterpillar Reman service | 1 | P0 |
| `PERK2     001` | Cannon Group Ldf | 3 | P0 |

### TUPY, preservación obligatoria (1 claves / 29 líneas)

| `customer_key_exact` | Nombre literal del archivo | Líneas | Prioridad |
|---|---|---:|---|
| `TUPY      000` | TECHNOCAST, SA DE CV | 29 | P0 |

### APTIV, identidad independiente (1 claves / 2 líneas)

| `customer_key_exact` | Nombre literal del archivo | Líneas | Prioridad |
|---|---|---:|---|
| `240625    001` | APTIV DURANGO II FV55 EDS | 2 | P1 |

### PECL, nombre por contrastar (1 claves / 3 líneas)

| `customer_key_exact` | Nombre literal del archivo | Líneas | Prioridad |
|---|---|---:|---|
| `PECL      001` | CANNON PACKAGING & LOGIST | 3 | P0 |

### Resto sin indicio CAT en nombre (57 claves / 1,252 líneas)

| `customer_key_exact` | Nombre literal del archivo | Líneas | Prioridad |
|---|---|---:|---|
| `00201000  001` | ATC DRIVETRAIN S9901 WEST | 13 | P2 |
| `11628500  018` | Bosch Rexroth Corp., Foun | 6 | P2 |
| `12395500  001` | B BRAUN MEDICAL INC | 1 | P2 |
| `18571001  039` | FCA US LLC MOPAR | 1 | P2 |
| `199300    000` | Borg Warner Morse TEC | 1 | P2 |
| `200128    001` | ATD AAM MAQUILADORA MEXIC | 1 | P2 |
| `23709300  000` | INDUSTRIAS JOHN DEERE ARG | 3 | P2 |
| `23829300  000` | Motores John Deere S.A. d | 36 | P2 |
| `24062000  041` | BORGWARNER ETTS MEXICO S | 3 | P2 |
| `240630    001` | BORGWARNER PROPULSION SYS | 1 | P2 |
| `28840900  000` | ELGIN INDUSTRIES | 5 | P2 |
| `31885100  001` | FORD LIVONIA TRANSMISSION | 10 | P2 |
| `328694    000` | ZF Suspension Technology | 71 | P2 |
| `4948401   000` | ZF POWERTRAIN MODULES SAL | 4 | P2 |
| `753984    000` | INTERNATIONAL MOTORS, LLC | 29 | P2 |
| `792861    001` | Valeo Kapec SA de CV | 5 | P2 |
| `813538    001` | PIONNER AUTOMOTIVE INDUST | 1 | P2 |
| `960118    001` | FEDERAL MOGUL MOTOPARTS, | 1 | P2 |
| `AAM       001` | AMERICAN AXLE & MANUFACTU | 5 | P2 |
| `AAM       002` | AMERICAN AXLE & MANUFACTU | 3 | P2 |
| `ADVAN     001` | MONROE MEXICO S DE RL DE | 230 | P2 |
| `BOLZONI   001` | BOLZONI AURAMO | 4 | P2 |
| `CELS      000` | CELS INC | 23 | P2 |
| `EATON     002` | EATON TRUCK COMPONENTS S | 70 | P2 |
| `EATON     300` | Eaton Truck Components S | 278 | P2 |
| `ENGINE00  000` | GENERAL ENGINE PRODUCTS, | 8 | P2 |
| `ETNBIEL   000` | EATON AUTOMOTIVE SYSTEMS | 12 | P2 |
| `ETNCEN    000` | EATON LOGISTIC CENTER C / | 2 | P2 |
| `ETNCORP   256` | EATON CORPORATION | 129 | P2 |
| `ETNCUM    001` | Eaton Cummins Automated | 26 | P2 |
| `ETNXI     000` | FAST EATON (XI'AN) DRIVET | 2 | P2 |
| `GMLUSA    001` | GM DAVISON ROAD 17177 PLA | 2 | P2 |
| `GMMLLC    002` | GENERAL MOTORS LLC | 2 | P2 |
| `JACOBS    001` | JACOBS VEHICLE SYSTEMS, I | 10 | P2 |
| `JASPER    000` | JASPER ENGINES & TRANSP-J | 2 | P2 |
| `KIBBLE    000` | KIBBLEWHITE PRECISION MAC | 1 | P2 |
| `LIB       000` | LIBERTY ENGINE PARTS, INC | 1 | P2 |
| `LINAMAR   000` | LINAMAR ESTON MANUFACTURI | 3 | P2 |
| `MAGNA     000` | MAGNA POWERTRAIN DE MEXIC | 29 | P2 |
| `MAGNEMX   001` | MARELLI TOLUCA MEXICO S D | 19 | P2 |
| `MARSH     000` | MARSHALL ENGINES, INC | 2 | P2 |
| `MERC      001` | MERCURY | 4 | P2 |
| `MILWAUKE  001` | Associated Spring Milwauk | 1 | P2 |
| `MTUAIKEN  001` | ROLLS-ROYCE SOLUTIONS AME | 6 | P2 |
| `PARKMOS   001` | PARKER INDUSTRIAL | 1 | P2 |
| `PEREZLOG  000` | INTERNATIONAL MOTORS, LLC | 1 | P2 |
| `REXR      001` | BOSCH REXROTH | 11 | P2 |
| `SHIELD    000` | Shield Restraint Systems | 12 | P2 |
| `SUMITOMO  000` | SUMITOMO NACCO FORKLIFT V | 1 | P2 |
| `TECMOSA   002` | Tecnología Modificada SA | 53 | P2 |
| `TECMOSA   003` | TECNOLOGIA MODIFICADA / D | 15 | P2 |
| `TEMX      001` | MONROE MEXICO S DE RL DE | 5 | P2 |
| `THYSSEN   000` | THYSSENKRUPP BILSTEIN | 30 | P2 |
| `TREMEC    000` | TRANSMISIONES Y EQUIPOS M | 4 | P2 |
| `VAL       001` | VALEO NORTH AMERICA, INC | 3 | P2 |
| `VEGE      000` | VEGE DE MEXICO | 45 | P2 |
| `ZFSLO     001` | ZF SLOVAKIA, A.S. | 5 | P2 |

## 3. Agenda concreta de decisiones para autoridad comercial

| Orden | Decisión pendiente | Identidades | Evidencia necesaria para cerrar |
|---:|---|---|---|
| 1 | Determinar pertenencia y exclusión Portal CAT de los cuatro especiales CAT/Perkins | `CATPERK   000`, `CATPERK   001`, `PERK1     001`, `PERK2     001` | Maestro comercial/cuenta de facturación, alcance del Portal, confirmación explícita por clave |
| 2 | Resolver la diferencia entre 19 y 20 candidatas de nombre CAT | `98753462  001`, además de las 19 de serie `170…` | Justificación documentada de cobertura o exclusión CAT por cada cuenta exacta |
| 3 | Incorporar preservación TUPY como prueba negativa | `TUPY      000` | Regla de negocio ya confirmada; verificar que la regla R3 nunca retire sus líneas |
| 4 | Aclarar PECL y nombres históricos divergentes | `PECL      001` | Identidad del maestro y evidencia de relación/alias, sin normalizar clientes |
| 5 | Confirmar APTIV sin fusionar DELPHI | `240625    001` y futura identidad DELPHI acreditada | Identificadores exactos; responsable CSR Carla confirmado por cuenta |
| 6 | Aprobar masivamente solo mediante acta con anexo exacto, no por default | Otras 57 identidades | Lista de claves de 13 caracteres + confirmación/no-CAT firmada por autoridad |

## 4. Contrato para cerrar R3 — condiciones binarias

1. La autoridad comercial asigna a **cada identidad exacta** un destino explícito: `EXCLUIR_ERP_POR_PORTAL_CAT` o `CONSERVAR_ERP` (o excepción documentada), con evidencia, aprobador y fecha. Estas etiquetas **son de expediente**, no un `rules.json` de importación listo.
2. TUPY preservada aun si se encuentra comercialmente asociada a CAT. Sin derivar cliente desde texto/prefijo; APTIV y DELPHI separados; PERK2 y PECL con identidad aclarada.
3. El catálogo controla nuevas identidades en SSRS: la presencia de una clave desconocida debe quedar `UNCLASSIFIED` y bloquear publicación, **nunca `OTHER` por descarte**. La identidad 85 detectada en el corte 06-nov se incorpora solo con evidencia exacta cuando se disponga de ella; no puede inventarse a partir de este archivo.
4. Cada clasificación se verifica contra las fuentes comerciales autorizadas; las líneas del TXT histórico pueden orientar pero **no** son entrada operativa ni base para reabrir SSRS vs TXT.
5. Una vez aprobada la matriz, crear artefacto R3 v2 y registro de aprobación con procedencia. **`CRC_APPROVED` solo después** de verificar contrato completo; no autofirmar. La implementación y el merge son fases posteriores autorizadas, con regresión focalizada y baseline guard.

## 5. Restricciones de ingeniería / alcance actual

- La guardia `NEXUS_BASELINE_PROTECTION_01` solo permite en R3 preaprobación `apps/nexus_junta_preview/DOCS/R3_*.md` y pruebas `app/tests/test_r3_*.py`. **No versionar este CSV en el worktree protegido** sin modificar formalmente el contrato; conservarlo en un área privada para revisión comercial.
- La rama protegida FIX6.2 permanece en commit `0cb87e75726ba30b57e2e229347758d18324f8e7`, tag `nexus/baseline-fix6.2-20261110`, y Frozen noviembre SHA256 `d5938411b58a06a65d978b31a64c9123a84869851c3af0d847dc1aebcc71fa28`. Prohibido modificar estos elementos.
- Mantener SSRS Current directo, FG ODBC, Embarques CFDI, Adelanto=0 por defecto y layout «Cambio Backlog» sin cambio. No ejecutar Refresh ni consultas corporativas para resolver esta preaprobación.
- El expediente no contiene una sola aprobación R3. Ninguna publicación corporativa autorizada ni modificación de `rules.json` ejecutable.

## 6. Punto preciso de continuación

Solicitar **una sola revisión comercial** de las excepciones P0 (4 CAT/Perkins + identidad `98753462  001` + PECL + preservación TUPY), seguida por validación del bloque de 19 candidatas de serie `170…` y luego las 57 restantes. Registrar decisiones por identidad exacta. No enviar 3,147 pedidos para revisión manual.

**Fuentes:** CSV recibido SHA256 `34c8a2097fed5d8a9d2c0ae5aeb003dd34d73ba1f3dbe12f968a8c24fc0550e7`; dictamen preparatorio R3/R1 de noviembre 2026; decisiones ya confirmadas de PULSO NEXUS. Todas las inferencias de triaje están marcadas como no aprobadas.
