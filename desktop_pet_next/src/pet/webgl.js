

/** Owns webgl behavior and its temporary runtime state. */
export function createWebglController({
  els,
  platform: platformPort,
  status: statusPort
}) {
  const { window } = platformPort;

  let webglProbe = null;

  function startWebglProbe() {
    if (webglProbe) {
      webglProbe.running = true;
      webglProbe.frame = requestAnimationFrame(renderWebglProbe);
      return;
    }

    const gl = els.canvas.getContext("webgl", {
      alpha: true,
      premultipliedAlpha: false,
      antialias: true
    });

    if (!gl) {
      statusPort.setStatus("WebGL unavailable");
      return;
    }

    const vertexSource = `
      attribute vec2 position;
      uniform float time;
      void main() {
        float sway = sin(time + position.y * 2.4) * 0.08;
        gl_Position = vec4(position.x + sway, position.y, 0.0, 1.0);
      }
    `;
    const fragmentSource = `
      precision mediump float;
      uniform float time;
      void main() {
        gl_FragColor = vec4(1.0, 0.28 + sin(time) * 0.12, 0.42, 0.72);
      }
    `;

    const program = createProgram(gl, vertexSource, fragmentSource);
    const buffer = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
    gl.bufferData(
      gl.ARRAY_BUFFER,
      new Float32Array([
        -0.22, -0.2,
        0.22, -0.2,
        0, 0.3
      ]),
      gl.STATIC_DRAW
    );

    webglProbe = {
      gl,
      program,
      buffer,
      position: gl.getAttribLocation(program, "position"),
      time: gl.getUniformLocation(program, "time"),
      running: true,
      startedAt: performance.now(),
      frame: 0
    };

    statusPort.setStatus("WebGL ready");
    webglProbe.frame = requestAnimationFrame(renderWebglProbe);
  }

  function renderWebglProbe(now) {
    if (!webglProbe?.running || !els.stage.classList.contains("show-webgl")) {
      if (webglProbe) webglProbe.running = false;
      return;
    }

    const { gl, program, position, time } = webglProbe;
    resizeCanvasToDisplaySize(els.canvas);
    gl.viewport(0, 0, els.canvas.width, els.canvas.height);
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.useProgram(program);
    gl.enableVertexAttribArray(position);
    gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0);
    gl.uniform1f(time, (now - webglProbe.startedAt) / 1000);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
    webglProbe.frame = requestAnimationFrame(renderWebglProbe);
  }

  function createProgram(gl, vertexSource, fragmentSource) {
    const vertex = compileShader(gl, gl.VERTEX_SHADER, vertexSource);
    const fragment = compileShader(gl, gl.FRAGMENT_SHADER, fragmentSource);
    const program = gl.createProgram();
    gl.attachShader(program, vertex);
    gl.attachShader(program, fragment);
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
      throw new Error(gl.getProgramInfoLog(program) ?? "WebGL link failed");
    }
    return program;
  }

  function compileShader(gl, type, source) {
    const shader = gl.createShader(type);
    gl.shaderSource(shader, source);
    gl.compileShader(shader);
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
      throw new Error(gl.getShaderInfoLog(shader) ?? "WebGL compile failed");
    }
    return shader;
  }

  function resizeCanvasToDisplaySize(canvas) {
    const width = Math.max(1, Math.floor(canvas.clientWidth * window.devicePixelRatio));
    const height = Math.max(1, Math.floor(canvas.clientHeight * window.devicePixelRatio));
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
    }
  }

  return {
    startWebglProbe
  };
}
