// Comportamiento de las pantallas. Los avisos de aquí solo orientan:
// las reglas presupuestarias se validan de nuevo en el servidor.
"use strict";

const leerMonto = (texto) => {
  const t = String(texto || "").trim().replace(/\s/g, "");
  if (!t) return 0;
  const n = Number(t.includes(",") ? t.replace(/\./g, "").replace(",", ".") : t.replace(/\.(?=\d{3}(\.|$))/g, ""));
  return Number.isFinite(n) ? n : NaN;
};

const bs = (n) => {
  const [entero, dec] = Math.abs(n).toFixed(2).split(".");
  return (n < 0 ? "-" : "") + entero.replace(/\B(?=(\d{3})+(?!\d))/g, ".") + "," + dec;
};

const datosPartidas = () => {
  const nodo = document.getElementById("datos-partidas");
  return nodo ? JSON.parse(nodo.textContent) : {};
};

// Agregar y quitar líneas dentro de cualquier contenedor .lineas.
function prepararLineas(raiz, alCambiar) {
  raiz.addEventListener("click", (e) => {
    const agregar = e.target.closest("button.agregar");
    if (agregar) {
      const caja = agregar.parentElement.querySelector(".lineas");
      const modelo = caja.querySelector(".linea");
      const nueva = modelo.cloneNode(true);
      nueva.querySelectorAll("input:not([type=hidden])").forEach((i) => (i.value = ""));
      nueva.querySelector(".estado-linea").textContent = "";
      caja.appendChild(nueva);
      nueva.querySelector("input[name=partida]").focus();
      alCambiar();
      return;
    }
    const quitar = e.target.closest("button.quitar");
    if (quitar) {
      const linea = quitar.closest(".linea");
      if (linea.parentElement.querySelectorAll(".linea").length > 1) linea.remove();
      else linea.querySelectorAll("input:not([type=hidden])").forEach((i) => (i.value = ""));
      alCambiar();
    }
  });
  raiz.addEventListener("input", alCambiar);
  raiz.addEventListener("change", alCambiar);
}

function marcar(linea, texto, clase) {
  const p = linea.querySelector(".estado-linea");
  p.textContent = texto;
  p.className = "estado-linea" + (clase ? " " + clase : "");
}

// ---------- Registrar gasto o ingreso ----------
// Saldo que limita cada operación y cómo se llama para el usuario.
const LIMITES = {
  PC: ["disponible", "Disponible hoy"],
  CS: ["disponible", "Disponible hoy"],
  CG: ["disponible", "Disponible hoy"],
  CCP: ["disponible", "Disponible hoy"],
  LP: ["precomprometido", "Precomprometido"],
  GC: ["por_causar", "Comprometido sin causar"],
  CP: ["por_causar", "Comprometido sin causar"],
  PG: ["por_pagar", "Causado sin pagar"],
  COB: ["por_cobrar", "Devengado sin recaudar"],
};

function prepararRegistro(form) {
  const datos = datosPartidas();
  const textoOperacion = () => {
    const r = form.querySelector("input[name=operacion_comun]:checked");
    return r ? [r.value, r.closest(".operacion").querySelector("b").textContent] : ["", "—"];
  };

  const revisar = () => {
    const [op, nombreOp] = textoOperacion();
    const limite = LIMITES[op];
    const usado = {};
    let total = 0, cuantas = 0, problemas = 0;
    form.querySelectorAll(".linea").forEach((linea) => {
      const clave = linea.querySelector("[name=partida]").value.trim();
      const monto = leerMonto(linea.querySelector("[name=monto]").value);
      if (!clave && !monto) return marcar(linea, "");
      const p = datos[clave];
      if (!p) {
        problemas++;
        return marcar(linea, clave ? "Esa partida no existe en este ejercicio. Elíjala de la lista." : "Falta la partida.", clave ? "mal" : "");
      }
      if (Number.isNaN(monto) || monto <= 0) {
        problemas++;
        return marcar(linea, p.nombre + " · escriba un monto válido, por ejemplo 1.250,50", "mal");
      }
      cuantas++;
      total += monto;
      if (!limite) return marcar(linea, p.nombre, "bien");
      usado[clave] = (usado[clave] || 0) + monto;
      const hay = Number(p[limite[0]]);
      const queda = hay - usado[clave];
      if (queda < -0.005) {
        problemas++;
        marcar(linea, `${p.nombre} · ${limite[1]}: ${bs(hay)}. No alcanza: faltan ${bs(-queda)}.`, "mal");
      } else {
        marcar(linea, `${p.nombre} · ${limite[1]}: ${bs(hay)} → quedará ${bs(queda)}`, "bien");
      }
    });
    form.querySelector("#res-operacion").textContent = nombreOp;
    form.querySelector("#res-partidas").textContent = cuantas;
    form.querySelector("#res-total").textContent = bs(total);
    form.querySelector("#guardar").textContent = problemas ? "Revise las partidas marcadas" : "Guardar";
  };

  prepararLineas(form, revisar);
  revisar();
}

// ---------- Modificaciones presupuestarias ----------
const TIPOS_MOD = {
  TRASPASO: {
    izq: ["Ceden (disminuyen)", "Partidas de las que sale el dinero. Deben tener disponibilidad.", "DI"],
    der: ["Reciben (aumentan)", "Partidas que reciben el dinero.", "AU"],
  },
  CREDITO: {
    izq: ["Lo financia (ingreso que aumenta)", "Opcional: la partida de ingreso que respalda el crédito, escrita como I:código.", "AU"],
    der: ["Reciben (aumentan)", "Partidas de gasto que reciben el crédito adicional.", "AU"],
  },
  RECTIFICA: {
    izq: ["Sale de la 4.98 (disminuye)", "Solo partidas de Rectificaciones al presupuesto (4.98).", "DI"],
    der: ["Reciben (aumentan)", "Partidas que reciben el dinero.", "AU"],
  },
  INSUBSIST: {
    izq: ["Se anulan (disminuyen)", "Créditos que se declaran insubsistentes. Deben tener disponibilidad.", "DI"],
    der: null,
  },
};

function prepararModificacion(form) {
  const datos = datosPartidas();
  const izq = form.querySelector("#col-ceden");
  const der = form.querySelector("#col-reciben");
  const tipo = () => form.querySelector("input[name=tipo]:checked").value;

  const columna = (col, conf) => {
    col.hidden = !conf;
    if (!conf) {
      col.querySelectorAll(".linea input:not([type=hidden])").forEach((i) => (i.value = ""));
      return;
    }
    col.querySelector(".titulo-col").textContent = conf[0];
    col.querySelector(".ayuda-col").textContent = conf[1];
    col.querySelectorAll("input[name=accion]").forEach((i) => (i.value = conf[2]));
  };

  const sumar = (col, revisarDisponible, soloCuenta) => {
    let suma = 0, mal = 0;
    col.querySelectorAll(".linea").forEach((linea) => {
      const clave = linea.querySelector("[name=partida]").value.trim();
      const monto = leerMonto(linea.querySelector("[name=monto]").value);
      if (!clave && !monto) return marcar(linea, "");
      const p = datos[clave];
      if (!p) { mal++; return marcar(linea, "Elija una partida de la lista.", "mal"); }
      if (Number.isNaN(monto) || monto <= 0) { mal++; return marcar(linea, p.nombre + " · monto no válido", "mal"); }
      suma += monto;
      const cuenta = clave.split("|")[1] || "";
      if (soloCuenta && !cuenta.startsWith(soloCuenta)) {
        mal++;
        return marcar(linea, `${p.nombre} · en una rectificación solo cede la ${soloCuenta.slice(0, -1)}.`, "mal");
      }
      if (revisarDisponible && p.disponible !== undefined) {
        const hay = Number(p.disponible);
        if (monto > hay + 0.005) { mal++; return marcar(linea, `${p.nombre} · disponible ${bs(hay)}: no alcanza.`, "mal"); }
        return marcar(linea, `${p.nombre} · disponible ${bs(hay)} → quedan ${bs(hay - monto)}`, "bien");
      }
      const actual = p.disponible !== undefined ? ` · disponible ${bs(Number(p.disponible))} → pasa a ${bs(Number(p.disponible) + monto)}` : "";
      marcar(linea, p.nombre + actual, "bien");
    });
    col.querySelector(".suma").textContent = bs(suma);
    return [suma, mal];
  };

  const revisar = () => {
    const t = tipo();
    const conf = TIPOS_MOD[t];
    columna(izq, conf.izq);
    columna(der, conf.der);
    document.getElementById("titulo-mod").textContent =
      form.querySelector("input[name=tipo]:checked").dataset.titulo;
    const [a, malA] = sumar(izq, conf.izq[2] === "DI", t === "RECTIFICA" ? "4.98." : "");
    const [b, malB] = conf.der ? sumar(der, false, "") : [0, 0];
    let bien = false, titulo, detalle;
    if (malA || malB) {
      titulo = "Hay partidas por corregir";
      detalle = "Revise los mensajes en rojo debajo de cada partida.";
    } else if (t === "INSUBSIST") {
      bien = a > 0;
      titulo = bien ? "Lista para enviar" : "Agregue las partidas";
      detalle = bien ? `Se anulan Bs. ${bs(a)} de créditos.` : "Indique qué créditos se declaran insubsistentes.";
    } else if (t === "CREDITO") {
      bien = b > 0 && (a === 0 || Math.abs(a - b) < 0.005);
      titulo = bien ? "El crédito cuadra" : b > 0 ? "El crédito no cuadra con su fuente" : "Agregue las partidas";
      detalle = bien ? `Aumenta el presupuesto en Bs. ${bs(b)}.` : b > 0 ? `El ingreso suma ${bs(a)} y el gasto ${bs(b)}.` : "Indique las partidas que reciben el crédito.";
    } else {
      bien = a > 0 && Math.abs(a - b) < 0.005;
      const nombre = t === "TRASPASO" ? "El traspaso" : "La rectificación";
      titulo = bien ? `${nombre} cuadra` : a || b ? `${nombre} no cuadra` : "Agregue las partidas";
      detalle = bien ? `Cede y recibe Bs. ${bs(a)}. Las cedentes tienen disponibilidad.` : a || b ? `Ceden ${bs(a)} y reciben ${bs(b)}: diferencia ${bs(Math.abs(a - b))}.` : "Se guarda en borrador; los saldos cambian cuando un administrador la aprueba.";
    }
    const caja = document.getElementById("verificacion");
    caja.classList.toggle("bien", bien);
    caja.classList.toggle("mal", !bien && (a > 0 || b > 0 || malA + malB > 0));
    document.getElementById("ver-titulo").textContent = titulo;
    document.getElementById("ver-detalle").textContent = detalle;
  };

  prepararLineas(form, revisar);
  revisar();
}

document.addEventListener("DOMContentLoaded", () => {
  // En pantallas angostas el menú empieza plegado.
  const menu = document.querySelector("details.menu");
  if (menu && window.matchMedia("(max-width: 900px)").matches) menu.open = false;
  document.querySelectorAll("select.auto-enviar").forEach((s) =>
    s.addEventListener("change", () => s.form.submit())
  );
  document.querySelectorAll("select.ir-a").forEach((s) =>
    s.addEventListener("change", () => (window.location.href = s.value))
  );
  document.querySelectorAll("form.confirmar").forEach((f) =>
    f.addEventListener("submit", (e) => {
      if (!confirm(f.dataset.mensaje || "¿Confirma?")) e.preventDefault();
    })
  );
  document.querySelectorAll("button.imprimir").forEach((b) =>
    b.addEventListener("click", () => window.print())
  );
  const registro = document.getElementById("registro");
  if (registro) prepararRegistro(registro);
  const modificacion = document.getElementById("modificacion");
  if (modificacion) prepararModificacion(modificacion);
});
