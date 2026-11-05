# Cierre de E1-A: referencia técnica congelada

**Dictamen independiente:** GO técnico para E1-A en su alcance **offline y con datos sintéticos**.

## Referencia congelada

| Elemento | Valor |
|---|---|
| Código | `62c5600d3904a02dfcc8f9025b43a540d26e8d09` |
| Evidencia | `1721bdc056607ccf90f9457ea55185c0a39d222e` (rama `claude/blissful-feynman-obiif0`) |
| Manifiesto | `docs/evidence/E1-A/MANIFEST.sha256` en `1721bdc`: 169 archivos; SHA-256 del propio manifiesto `79a13fd4f6962b3174a8d796528449347c4746ed2eb2a8ae4921352ed6f2a174` |
| CI de referencia | [run 42076454439](https://github.com/mcrisnidh/pulso-nexus/actions/runs/42076454439): Linux ✅ y Windows ✅ (`229 passed, 5 skipped`) |
| Informes | `docs/evidence/E1-A/REPORT.md` (histórico), `docs/evidence/E1-A-R1/REPORT.md`, `docs/evidence/E1-A-R1b/REPORT.md` |

## Reglas de congelación

- Este documento se añade **después** de `1721bdc` y no modifica ni el código ni el manifiesto congelados. `sha256sum -c docs/evidence/E1-A/MANIFEST.sha256` sigue verificando la evidencia de `1721bdc`.
- Cualquier cambio posterior en `src/nexus/storage/`, `src/nexus/pipeline/` o en los contratos sintéticos abre una revisión nueva. No altera la referencia de E1-A.
- **Propuesta:** proteger la referencia con una etiqueta anotada `e1a-baseline` sobre `1721bdc`. **No se ha creado ni publicado** porque la autorización vigente se limita a la rama de desarrollo. Queda pendiente de autorización expresa.

## Deuda documental (registrada, no corregida en E1-A)

| ID | Ubicación | Imprecisión | Precisión correcta |
|---|---|---|---|
| **DD-1** | `src/nexus/storage/_fs.py::ensure_private_dir` (≈ l. 391); `docs/evidence/E1-A-R1b/REPORT.md` §3 (G2); `PLATFORM_GUARANTEES.md` | La documentación dice "árbol `0700`" | El modo `0700` solo se **aplica** a los directorios que **crea** el almacén. Una raíz `objects/` o `staging/` **preexistente** se acepta con cualquier modo que no tenga escritura de grupo u otros (por ejemplo `0755` o `0750`). Los directorios **intermedios** preexistentes bajo `objects/` (por ejemplo, creados con `0755` antes de `62c5600`) **no se validan ni se endurecen**: `mkdir` con `FileExistsError` suprimido conserva su modo, y la cadena no comprueba propietario ni modo de los intermedios |
| **DD-2** | `src/nexus/storage/_fs.py` comentario NX-E1A-03 (≈ l. 363–375) | "Solo ese uid o root pueden relocalizar" | Pueden relocalizar: (a) el uid del almacén; (b) root o procesos con `CAP_DAC_OVERRIDE`/`CAP_FOWNER`; (c) **cualquier principal con escritura sobre algún ancestro** de la raíz del almacén, que podría mover la raíz completa. Los ancestros son de confianza **por hipótesis de despliegue**, no por control del almacén. Además, en un directorio con *sticky bit* aplican reglas de propiedad adicionales |

Ninguna de las dos deudas cambia las garantías ni los resultados de las pruebas: G2 se demostró con una raíz creada por el propio almacén. Ambas deben corregirse en la siguiente revisión que toque `storage/`, con una prueba de regresión para DD-1.

## Límites reconocidos por el dictamen

- Permisos e identidad de servicio en el despliegue real (P5, P13).
- ACL de Windows (P13).
- Durabilidad física: no se ha demostrado resistencia a cortes eléctricos; en Windows no hay `fsync` de directorio.
- Administradores privilegiados (P16, E1-C).
