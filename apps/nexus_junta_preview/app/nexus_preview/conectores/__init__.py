"""Conectores corporativos de SOLO LECTURA de PULSO NEXUS v0.3 (Backlog SSRS, FG ODBC, Embarques).

Principios (no negociables):
- Cada conector usa SU identidad: SSRS = sesión Windows del usuario (``ucbaa``) sin manejar
  contraseñas; FG = credencial ``remcb``; Embarques = credencial ``sm0003``. Las credenciales se
  leen del Administrador de credenciales de Windows en memoria, nunca de archivos, código, logs ni
  paquetes, y nunca se intercambian (la cuenta de la credencial debe coincidir con la esperada).
- Solo SELECT constantes y parametrizadas; conexión en modo solo lectura; sin DML/DDL.
- Una fuente fallida no se convierte en vacío ni en ceros: se informa y se aplica la política
  explícita del conector.
- Nada se activa sin configuración explícita y sin acreditación (conciliación PASS).
"""


class ConectorError(Exception):
    """Fallo de un conector con un código estable y un mensaje SIN secretos."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail
