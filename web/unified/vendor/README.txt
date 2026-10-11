three.module.min.js — minified subset of three.js r186 (npm three@0.186.1, MIT, see THREE-LICENSE.txt).
SHA-256: 4d46297fbb202a3c484fabc2e410ebe6e23e6e687fa9943b8b952fdb028cf686

Rebuild (esbuild 0.28.2, npm @esbuild/linux-x64):

  npm pack three@0.186.1 @esbuild/linux-x64@0.28.2
  mkdir -p node_modules/three && tar -xzf three-0.186.1.tgz -C node_modules/three --strip-components=1
  tar -xzf esbuild-linux-x64-0.28.2.tgz
  package/bin/esbuild entry.js --bundle --minify --format=esm --legal-comments=inline --outfile=three.module.min.js

entry.js:

  export {
    WebGLRenderer, Scene, PerspectiveCamera, Group, Mesh, LineSegments, Points,
    BoxGeometry, SphereGeometry, BufferGeometry, InstancedBufferGeometry,
    BufferAttribute, Float32BufferAttribute, InstancedBufferAttribute,
    ShaderMaterial, MeshBasicMaterial, LineBasicMaterial, PointsMaterial,
    Color, Vector2, Vector3, Raycaster, Sphere,
    AdditiveBlending, NormalBlending, BackSide, FrontSide, DoubleSide, SRGBColorSpace, REVISION
  } from 'three';
