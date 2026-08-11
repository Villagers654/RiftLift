#version 450
layout(location = 0) out vec2 outTexCoord;
layout(push_constant, std430) uniform pc {
	vec4 texBounds; // uMin, uMax, vMin, vMax
};

vec2 positions[4] = vec2[](
	vec2(-1.0, -1.0),
	vec2(-1.0, 1.0),
	vec2(1.0, -1.0),
	vec2(1.0, 1.0)
);

vec2 texCoords[4] = vec2[](
	texBounds.xz,
	texBounds.xw,
	texBounds.yz,
	texBounds.yw
);

void main() {
	gl_Position = vec4(positions[gl_VertexIndex], 0.0f, 1.0f);
	outTexCoord = texCoords[gl_VertexIndex];
}
