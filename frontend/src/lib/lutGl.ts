/** Parse Adobe/.cube 3D LUTs and apply them in WebGL2 for timeline preview. */

export interface ParsedCube {
  size: number
  /** RGB triplets, R varies fastest, then G, then B (matches our generator + FFmpeg). */
  data: Float32Array
}

export function parseCube(text: string): ParsedCube {
  const lines = text.split(/\r?\n/)
  let size = 0
  const values: number[] = []
  for (const raw of lines) {
    const line = raw.trim()
    if (!line || line.startsWith('#') || line.startsWith('TITLE')) continue
    if (line.startsWith('LUT_3D_SIZE')) {
      size = Number(line.split(/\s+/)[1])
      continue
    }
    if (line.startsWith('DOMAIN_')) continue
    const parts = line.split(/\s+/).map(Number)
    const r = parts[0]
    const g = parts[1]
    const b = parts[2]
    if (
      parts.length >= 3 &&
      r !== undefined &&
      g !== undefined &&
      b !== undefined &&
      Number.isFinite(r) &&
      Number.isFinite(g) &&
      Number.isFinite(b)
    ) {
      values.push(r, g, b)
    }
  }
  if (size < 2) throw new Error('Invalid LUT_3D_SIZE')
  const expected = size * size * size * 3
  if (values.length < expected) throw new Error(`Incomplete .cube data (${values.length}/${expected})`)
  return { size, data: Float32Array.from(values.slice(0, expected)) }
}

function identityCube(size = 17): ParsedCube {
  const data = new Float32Array(size * size * size * 3)
  let i = 0
  for (let b = 0; b < size; b++) {
    for (let g = 0; g < size; g++) {
      for (let r = 0; r < size; r++) {
        data[i++] = r / (size - 1)
        data[i++] = g / (size - 1)
        data[i++] = b / (size - 1)
      }
    }
  }
  return { size, data }
}

const VERT = `#version 300 es
in vec2 aPos;
out vec2 vUv;
void main() {
  vUv = aPos * 0.5 + 0.5;
  gl_Position = vec4(aPos, 0.0, 1.0);
}`

const FRAG = `#version 300 es
precision highp float;
precision highp sampler3D;
in vec2 vUv;
out vec4 outColor;
uniform sampler2D uImage;
uniform sampler3D uLut;
uniform vec4 uUvRect; // u0 v0 u1 v1 in source space
uniform float uBoost;
uniform float uApplyLut;
uniform float uFlipY;

vec3 applyBoost(vec3 c, float s) {
  if (abs(s) < 1e-4) return c;
  float contrast = max(0.3, 1.0 + 0.14 * s);
  float saturation = max(0.1, 1.0 + 0.32 * s);
  float brightness = clamp(1.0 + 0.015 * s, 0.85, 1.15);
  c *= brightness;
  c = (c - 0.5) * contrast + 0.5;
  float luma = dot(c, vec3(0.2126, 0.7152, 0.0722));
  c = luma + saturation * (c - luma);
  return clamp(c, 0.0, 1.0);
}

void main() {
  float y = mix(vUv.y, 1.0 - vUv.y, uFlipY);
  vec2 uv = vec2(
    mix(uUvRect.x, uUvRect.z, vUv.x),
    mix(uUvRect.y, uUvRect.w, y)
  );
  vec4 src = texture(uImage, uv);
  vec3 c = applyBoost(src.rgb, uBoost);
  if (uApplyLut > 0.5) {
    c = texture(uLut, clamp(c, 0.0, 1.0)).rgb;
  }
  outColor = vec4(c, src.a);
}`

function compile(gl: WebGL2RenderingContext, type: number, src: string): WebGLShader {
  const sh = gl.createShader(type)
  if (!sh) throw new Error('createShader failed')
  gl.shaderSource(sh, src)
  gl.compileShader(sh)
  if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) {
    const info = gl.getShaderInfoLog(sh) || 'shader error'
    gl.deleteShader(sh)
    throw new Error(info)
  }
  return sh
}

export type LutSource = HTMLVideoElement | HTMLImageElement

export interface LutDrawParams {
  fit: 'cover' | 'contain'
  focalX: number
  focalY: number
  boost: number
  applyLut: boolean
}

/** object-fit:cover UV window into the source texture. */
export function coverUvRect(
  srcW: number,
  srcH: number,
  dstW: number,
  dstH: number,
  focalX = 0.5,
  focalY = 0.5,
): { u0: number; v0: number; u1: number; v1: number } {
  if (srcW <= 0 || srcH <= 0 || dstW <= 0 || dstH <= 0) {
    return { u0: 0, v0: 0, u1: 1, v1: 1 }
  }
  const srcAspect = srcW / srcH
  const dstAspect = dstW / dstH
  let cropW: number
  let cropH: number
  if (srcAspect > dstAspect) {
    cropH = 1
    cropW = dstAspect / srcAspect
  } else {
    cropW = 1
    cropH = srcAspect / dstAspect
  }
  const fx = Math.min(1, Math.max(0, focalX))
  const fy = Math.min(1, Math.max(0, focalY))
  const u0 = (1 - cropW) * fx
  const v0 = (1 - cropH) * fy
  return { u0, v0, u1: u0 + cropW, v1: v0 + cropH }
}

export class LutGlRenderer {
  readonly canvas: HTMLCanvasElement
  private gl: WebGL2RenderingContext | null
  private program: WebGLProgram | null = null
  private vao: WebGLVertexArrayObject | null = null
  private imageTex: WebGLTexture | null = null
  private lutTex: WebGLTexture | null = null
  private locs: {
    uImage: WebGLUniformLocation | null
    uLut: WebGLUniformLocation | null
    uUvRect: WebGLUniformLocation | null
    uBoost: WebGLUniformLocation | null
    uApplyLut: WebGLUniformLocation | null
    uFlipY: WebGLUniformLocation | null
  } = {
    uImage: null,
    uLut: null,
    uUvRect: null,
    uBoost: null,
    uApplyLut: null,
    uFlipY: null,
  }
  private raf = 0
  private source: LutSource | null = null
  private running = false
  private lutSize = 17
  private failed = false

  static isSupported(): boolean {
    try {
      const c = document.createElement('canvas')
      return Boolean(c.getContext('webgl2'))
    } catch {
      return false
    }
  }

  constructor(canvas: HTMLCanvasElement) {
    this.canvas = canvas
    this.gl = canvas.getContext('webgl2', {
      alpha: false,
      antialias: false,
      preserveDrawingBuffer: false,
      powerPreference: 'low-power',
    })
    if (!this.gl) {
      this.failed = true
      return
    }
    try {
      this.init(this.gl)
      this.setLutCube(identityCube(17))
    } catch {
      this.failed = true
      this.gl = null
    }
  }

  get ok(): boolean {
    return Boolean(this.gl) && !this.failed
  }

  private init(gl: WebGL2RenderingContext) {
    const vs = compile(gl, gl.VERTEX_SHADER, VERT)
    const fs = compile(gl, gl.FRAGMENT_SHADER, FRAG)
    const prog = gl.createProgram()
    if (!prog) throw new Error('createProgram failed')
    gl.attachShader(prog, vs)
    gl.attachShader(prog, fs)
    gl.linkProgram(prog)
    gl.deleteShader(vs)
    gl.deleteShader(fs)
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) {
      throw new Error(gl.getProgramInfoLog(prog) || 'link failed')
    }
    this.program = prog
    gl.useProgram(prog)

    const buf = gl.createBuffer()
    gl.bindBuffer(gl.ARRAY_BUFFER, buf)
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW)
    this.vao = gl.createVertexArray()
    gl.bindVertexArray(this.vao)
    const aPos = gl.getAttribLocation(prog, 'aPos')
    gl.enableVertexAttribArray(aPos)
    gl.vertexAttribPointer(aPos, 2, gl.FLOAT, false, 0, 0)

    this.imageTex = gl.createTexture()
    gl.activeTexture(gl.TEXTURE0)
    gl.bindTexture(gl.TEXTURE_2D, this.imageTex)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR)

    this.lutTex = gl.createTexture()
    gl.activeTexture(gl.TEXTURE1)
    gl.bindTexture(gl.TEXTURE_3D, this.lutTex)
    gl.texParameteri(gl.TEXTURE_3D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE)
    gl.texParameteri(gl.TEXTURE_3D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE)
    gl.texParameteri(gl.TEXTURE_3D, gl.TEXTURE_WRAP_R, gl.CLAMP_TO_EDGE)
    gl.texParameteri(gl.TEXTURE_3D, gl.TEXTURE_MIN_FILTER, gl.LINEAR)
    gl.texParameteri(gl.TEXTURE_3D, gl.TEXTURE_MAG_FILTER, gl.LINEAR)

    this.locs = {
      uImage: gl.getUniformLocation(prog, 'uImage'),
      uLut: gl.getUniformLocation(prog, 'uLut'),
      uUvRect: gl.getUniformLocation(prog, 'uUvRect'),
      uBoost: gl.getUniformLocation(prog, 'uBoost'),
      uApplyLut: gl.getUniformLocation(prog, 'uApplyLut'),
      uFlipY: gl.getUniformLocation(prog, 'uFlipY'),
    }
    gl.uniform1i(this.locs.uImage, 0)
    gl.uniform1i(this.locs.uLut, 1)
  }

  setSource(source: LutSource | null) {
    this.source = source
  }

  setLutCube(cube: ParsedCube) {
    const gl = this.gl
    if (!gl || !this.lutTex) return
    this.lutSize = cube.size
    const u8 = new Uint8Array(cube.data.length)
    for (let i = 0; i < cube.data.length; i++) {
      u8[i] = Math.round(Math.min(1, Math.max(0, cube.data[i] ?? 0)) * 255)
    }
    gl.activeTexture(gl.TEXTURE1)
    gl.bindTexture(gl.TEXTURE_3D, this.lutTex)
    // Tightly packed RGB rows (size * 3 bytes) are rarely 4-byte aligned; the default alignment rejects the upload.
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1)
    gl.texImage3D(
      gl.TEXTURE_3D,
      0,
      gl.RGB,
      cube.size,
      cube.size,
      cube.size,
      0,
      gl.RGB,
      gl.UNSIGNED_BYTE,
      u8,
    )
  }

  setIdentityLut() {
    this.setLutCube(identityCube(this.lutSize || 17))
  }

  draw(params: LutDrawParams) {
    const gl = this.gl
    const src = this.source
    if (!gl || !this.program || !src) return

    const cssW = Math.max(1, Math.round(this.canvas.clientWidth * (window.devicePixelRatio || 1)))
    const cssH = Math.max(1, Math.round(this.canvas.clientHeight * (window.devicePixelRatio || 1)))
    if (this.canvas.width !== cssW || this.canvas.height !== cssH) {
      this.canvas.width = cssW
      this.canvas.height = cssH
    }

    let srcW = 0
    let srcH = 0
    let flipY = 0
    if (src instanceof HTMLVideoElement) {
      srcW = src.videoWidth
      srcH = src.videoHeight
      flipY = 1
      if (srcW < 2 || src.readyState < 2) return
    } else {
      srcW = src.naturalWidth
      srcH = src.naturalHeight
      flipY = 0
      if (srcW < 2 || !src.complete) return
    }

    const dstW = this.canvas.width
    const dstH = this.canvas.height
    gl.viewport(0, 0, dstW, dstH)
    gl.clearColor(0, 0, 0, 1)
    gl.clear(gl.COLOR_BUFFER_BIT)

    const fx = Math.min(1, Math.max(0, params.focalX))
    const fy = Math.min(1, Math.max(0, params.focalY))
    let uv = { u0: 0, v0: 0, u1: 1, v1: 1 }
    if (params.fit === 'contain') {
      const scale = Math.min(dstW / srcW, dstH / srcH)
      const w = Math.max(1, Math.round(srcW * scale))
      const h = Math.max(1, Math.round(srcH * scale))
      const x = Math.round((dstW - w) * fx)
      // WebGL viewport origin is bottom-left.
      const y = Math.round((dstH - h) * (1 - fy))
      gl.viewport(x, y, w, h)
    } else {
      uv = coverUvRect(srcW, srcH, dstW, dstH, fx, fy)
    }

    gl.useProgram(this.program)
    gl.bindVertexArray(this.vao)

    gl.activeTexture(gl.TEXTURE0)
    gl.bindTexture(gl.TEXTURE_2D, this.imageTex)
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, 0)
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, src)

    gl.uniform4f(this.locs.uUvRect, uv.u0, uv.v0, uv.u1, uv.v1)
    gl.uniform1f(this.locs.uBoost, params.boost)
    gl.uniform1f(this.locs.uApplyLut, params.applyLut ? 1 : 0)
    gl.uniform1f(this.locs.uFlipY, flipY)

    gl.activeTexture(gl.TEXTURE1)
    gl.bindTexture(gl.TEXTURE_3D, this.lutTex)

    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4)
  }

  start(getParams: () => LutDrawParams) {
    if (!this.ok || this.running) return
    this.running = true
    const tick = () => {
      if (!this.running) return
      try {
        this.draw(getParams())
      } catch {
        /* ignore transient texture errors */
      }
      this.raf = requestAnimationFrame(tick)
    }
    this.raf = requestAnimationFrame(tick)
  }

  stop() {
    this.running = false
    if (this.raf) cancelAnimationFrame(this.raf)
    this.raf = 0
  }

  dispose() {
    this.stop()
    const gl = this.gl
    if (gl) {
      if (this.imageTex) gl.deleteTexture(this.imageTex)
      if (this.lutTex) gl.deleteTexture(this.lutTex)
      if (this.program) gl.deleteProgram(this.program)
      if (this.vao) gl.deleteVertexArray(this.vao)
    }
    this.gl = null
    this.source = null
  }
}

const cubeCache = new Map<string, Promise<ParsedCube>>()

export function lutCubeUrl(lutId: string, strength = 1): string {
  const s = Math.max(0, Math.min(1, strength))
  const pct = Math.round(s * 20) * 5
  return `/api/timeline/luts/${encodeURIComponent(lutId)}/cube?strength=${(pct / 100).toFixed(2)}`
}

export function fetchLutCube(lutId: string, strength = 1): Promise<ParsedCube> {
  const s = Math.max(0, Math.min(1, strength))
  const pct = Math.round(s * 20) * 5
  if (!lutId || lutId === 'none' || pct <= 0) return Promise.resolve(identityCube(17))
  const key = `${lutId}_${pct}`
  let pending = cubeCache.get(key)
  if (!pending) {
    pending = fetch(lutCubeUrl(lutId, pct / 100))
      .then(async (res) => {
        if (!res.ok) throw new Error(`LUT fetch ${res.status}`)
        return parseCube(await res.text())
      })
      .catch((err) => {
        cubeCache.delete(key)
        throw err
      })
    cubeCache.set(key, pending)
  }
  return pending
}
