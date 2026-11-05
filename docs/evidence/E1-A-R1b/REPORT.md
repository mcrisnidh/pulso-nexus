# PULSO NEXUS — E1-A-R1b: NX-E1A-03 (relocalización externa de directorios)

| Campo | Valor |
|---|---|
| Alcance | Solo NX-E1A-03. Sin SQL, sin E1-B ni E1-C, sin infraestructura corporativa |
| Commit de reproducción (rojo a propósito) | `33765ae`: solo añade la sonda; el código de producción es el de `e401cf6` |
| Commit de corrección (código auditado) | **`62c5600d3904a02dfcc8f9025b43a540d26e8d09`** |
| CI de reproducción | [run 42075752507](https://github.com/mcrisnidh/pulso-nexus/actions/runs/42075752507). Linux ❌ `2 failed, 227 passed` (`moved/x.bin`). Windows ✅ (el bloqueo de directorios ya impedía la relocalización) |
| CI de la corrección | [run 42076454439](https://github.com/mcrisnidh/pulso-nexus/actions/runs/42076454439). **Linux ✅ (todos los pasos). Windows ✅ `229 passed, 5 skipped`** |
| Local (clonaciones limpias de `62c5600`, `autocrlf=false/true`) | 15/15 pasos `exit 0` en ambas, **234/234** pruebas, cobertura 99 % (`local/lf`, `local/crlf`) |

## 1. Sonda y resultado anterior

`tests/adversarial/test_relocation.py::test_external_relocation_during_publish`, ejecutada contra la producción sin modificar (`relocation_before_fix.txt`):

1. Prepara `store/objects/a/` y `outside/` en el mismo sistema de archivos.
2. En `fault_hook("before_publish")` ejecuta `os.rename(store/objects/a, outside/moved)`.
3. En la variante `move+symlink`, coloca además un symlink en `store/objects/a`.

**Resultado:** 2/2 fallan. En ambas variantes, `put` **declara éxito** (`ObjectRef`) y el objeto queda en `outside/moved/x.bin`. En CI ocurre lo mismo en Linux. En Windows no se reproduce, porque el directorio está abierto sin `FILE_SHARE_DELETE` y el renombrado se rechaza.

## 2. Causa raíz e implicaciones para el modelo de amenazas

- En POSIX un descriptor de directorio identifica un **inodo**, no una posición en el árbol. `linkat(dst_dir_fd)` escribe en ese inodo esté donde esté. La defensa de R1 (no seguir enlaces) es correcta frente a symlinks y junctions, pero **no es contención física**.
- Ningún mecanismo POSIX en espacio de usuario impide que otro proceso con permiso de escritura sobre el árbol mueva un directorio abierto.
- Cualquier comprobación de ruta (volver a recorrer por nombre o `linkat` con una ruta de varios componentes) **conserva una carrera equivalente**: el directorio puede moverse justo después de comprobarlo. Incluso dentro del kernel, el renombrado de un directorio no bloquea el inodo del directorio movido.
- **Implicación:** la contención física solo puede garantizarse **impidiendo que un principal no confiable tenga escritura sobre el árbol**. Si un actor la tiene (el mismo uid del almacén o root), pertenece al dominio de confianza del almacén: igual que podría borrar o reescribir objetos, puede relocalizarlos. Ese caso se **detecta**, no se previene, y queda cubierto por el modelo de administrador privilegiado (P16, E1-C).

## 3. Corrección: garantías separadas

| Garantía | Mecanismo | Naturaleza | Evidencia |
|---|---|---|---|
| **G1. No seguir symlinks ni junctions** (R1) | `openat`/`linkat` con `O_NOFOLLOW`; en Windows, rechazo de puntos de reanálisis | Prevención (sin cambios) | `test_toctou.py` 6/6 en Linux y Windows |
| **G2. Contención física frente a relocalización: control EFECTIVO** | Aislamiento por permisos del SO. `ensure_private_dir` crea el árbol con modo `0700` (independiente de la umask) y **rechaza** raíces que no sean del uid efectivo o que sean escribibles por grupo u otros. Los directorios intermedios se crean con `0700` | Prevención frente a principales distintos del almacén | `test_permission_isolation_prevents_relocation_by_other_principal`: un **segundo usuario real** (`nobody`, uid 65534; `setuid` como root, o `sudo -n` en el runner de CI) recibe `EACCES` al intentar mover `objects/a`, y el objeto queda dentro. **Control positivo:** el mismo atacante **sí** relocaliza un árbol permisivo, lo que demuestra que tiene la capacidad |
| **G3. No declarar éxito sin contención** | Tras publicar, se recorre de nuevo `objects/…` sin seguir enlaces y se compara `(st_dev, st_ino)` con el descriptor retenido. Si difiere: se retira el enlace propio (**solo** si su identidad coincide con el objeto creado) e `IntegrityError` ("frontera de almacenamiento perdida") | **Detección, no prevención.** Conserva una carrera residual | Sonda NX-E1A-03: 2/2 pasan (`relocation_after_fix.txt`); `test_rollback_never_removes_foreign_entry` |
| **G4. Windows** | La cadena de directorios queda bloqueada sin `FILE_SHARE_DELETE` durante la operación | Prevención mientras dura la operación | CI Windows: la relocalización se rechaza; las sondas pasan |

### Lo que G3 no garantiza (declarado y probado)

- **Existencia transitoria fuera del almacén (POSIX).** Entre `linkat` y la retirada, `outside/moved/x.bin` **existe durante un instante**. La sonda comprueba el estado final y la ausencia de éxito, no la inexistencia en todo momento. Solo G2 impide su creación.
- **Carrera residual.** Si el directorio se relocaliza **después** de la comprobación, el éxito ya declarado no se revierte. `test_residual_race_after_check_is_detected_not_prevented` lo demuestra: el objeto acaba fuera y la siguiente lectura falla cerrada (`FileNotFoundError`). Equivale a manipular el almacén después de publicar.

Por estos dos puntos, G3 **no se presenta como solución**: la solución efectiva es G2.

## 4. Pruebas de regresión añadidas

| Prueba | Qué fija |
|---|---|
| `test_external_relocation_during_publish[move]` y `[move+symlink]` | La sonda de la revisión: nada nuevo fuera y ningún éxito sin contención |
| `test_permission_isolation_prevents_relocation_by_other_principal` | G2 con un principal real distinto y control positivo. Si el entorno no puede crear un segundo principal (ni root ni `sudo -n`), la prueba **falla**, no se omite |
| `test_residual_race_after_check_is_detected_not_prevented` | Límite declarado de G3 (POSIX) y bloqueo en Windows |
| `test_store_tree_is_private_and_insecure_roots_are_refused` | Modos `0700` del árbol; rechazo de raíces `0777` y de raíces de otro uid |
| `test_ensure_private_dir_rejects_non_directory` y `test_rollback_never_removes_foreign_entry` | Validación de la raíz; la retirada nunca borra un archivo ajeno |

Los escenarios y criterios anteriores no cambian: `test_toctou.py`, `test_concurrency.py`, `test_crash_injection.py` y A1–A14 siguen pasando sin modificaciones.

## 5. Riesgos residuales

1. **Actor con el uid del almacén o root:** puede relocalizar (y modificar) el árbol. Se detecta, no se previene (P16, E1-C).
2. **Windows:** las ACL del árbol no se validan (P13). El bloqueo solo protege mientras dura cada operación.
3. **Despliegue:** G2 depende de que el almacén se ejecute con una identidad de servicio propia y de que **ningún otro principal** tenga escritura sobre su raíz ni sobre sus ancestros. Los ancestros siguen siendo de confianza. Debe verificarse en el entorno real (P5, P13).
