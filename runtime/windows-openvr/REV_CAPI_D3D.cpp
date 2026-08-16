#include "OVR_CAPI_D3D.h"
#include "Common.h"
#include "Session.h"
#include "CompositorD3D.h"
#include "TextureD3D.h"

#include <d3d11.h>

OVR_PUBLIC_FUNCTION(ovrResult) ovr_CreateTextureSwapChainDX(ovrSession session,
                                                            IUnknown* d3dPtr,
                                                            const ovrTextureSwapChainDesc* desc,
                                                            ovrTextureSwapChain* out_TextureSwapChain)
{
	REV_TRACE(ovr_CreateTextureSwapChainDX);

	if (!session)
		return ovrError_InvalidSession;

	if (!d3dPtr || !desc || !out_TextureSwapChain)
		return ovrError_InvalidParameter;

	TraceOculusValue("ovr_CreateTextureSwapChainDX.Type", desc->Type);
	TraceOculusValue("ovr_CreateTextureSwapChainDX.Format", desc->Format);
	TraceOculusValue("ovr_CreateTextureSwapChainDX.ArraySize", desc->ArraySize);
	TraceOculusValue("ovr_CreateTextureSwapChainDX.Width", desc->Width);
	TraceOculusValue("ovr_CreateTextureSwapChainDX.Height", desc->Height);
	TraceOculusValue("ovr_CreateTextureSwapChainDX.MipLevels", desc->MipLevels);
	TraceOculusValue("ovr_CreateTextureSwapChainDX.SampleCount", desc->SampleCount);
	TraceOculusValue("ovr_CreateTextureSwapChainDX.StaticImage", desc->StaticImage);
	TraceOculusValue("ovr_CreateTextureSwapChainDX.MiscFlags", desc->MiscFlags);
	TraceOculusValue("ovr_CreateTextureSwapChainDX.BindFlags", desc->BindFlags);

	if (!session->Compositor)
	{
		session->Compositor.reset(CompositorD3D::Create(d3dPtr));
		if (!session->Compositor)
			return ovrError_RuntimeException;
	}

	if (session->Compositor->GetAPI() != vr::TextureType_DirectX)
		return ovrError_RuntimeException;

	ovrResult result = session->Compositor->CreateTextureSwapChain(desc, out_TextureSwapChain);
	TraceOculusValue("ovr_CreateTextureSwapChainDX.result", result);
	return result;
}

OVR_PUBLIC_FUNCTION(ovrResult) ovr_GetTextureSwapChainBufferDX(ovrSession session,
                                                               ovrTextureSwapChain chain,
                                                               int index,
                                                               IID iid,
                                                               void** out_Buffer)
{
	REV_TRACE(ovr_GetTextureSwapChainBufferDX);

	if (!session)
		return ovrError_InvalidSession;

	if (!chain || !out_Buffer)
		return ovrError_InvalidParameter;

	if (index < 0)
		index = chain->CurrentIndex;

	TextureD3D* texture = dynamic_cast<TextureD3D*>(chain->Textures[index].get());
	if (!texture)
		return ovrError_InvalidParameter;

	HRESULT hr = texture->Texture()->QueryInterface(iid, out_Buffer);
	if (FAILED(hr))
		return ovrError_InvalidParameter;

	return ovrSuccess;
}

OVR_PUBLIC_FUNCTION(ovrResult) ovr_CreateMirrorTextureDX(ovrSession session,
                                                         IUnknown* d3dPtr,
                                                         const ovrMirrorTextureDesc* desc,
                                                         ovrMirrorTexture* out_MirrorTexture)
{
	REV_TRACE(ovr_CreateMirrorTextureDX);

	if (!session)
		return ovrError_InvalidSession;

	if (!d3dPtr || !desc || !out_MirrorTexture)
		return ovrError_InvalidParameter;

	if (!session->Compositor)
	{
		session->Compositor.reset(CompositorD3D::Create(d3dPtr));
		if (!session->Compositor)
			return ovrError_RuntimeException;
	}

	if (session->Compositor->GetAPI() != vr::TextureType_DirectX)
		return ovrError_RuntimeException;

	return session->Compositor->CreateMirrorTexture(desc, out_MirrorTexture);
}

OVR_PUBLIC_FUNCTION(ovrResult) ovr_CreateMirrorTextureWithOptionsDX(ovrSession session,
                                                                    IUnknown* d3dPtr,
                                                                    const ovrMirrorTextureDesc* desc,
                                                                    ovrMirrorTexture* out_MirrorTexture)
{
	return ovr_CreateMirrorTextureDX(session, d3dPtr, desc, out_MirrorTexture);
}

OVR_PUBLIC_FUNCTION(ovrResult) ovr_GetMirrorTextureBufferDX(ovrSession session,
                                                            ovrMirrorTexture mirrorTexture,
                                                            IID iid,
                                                            void** out_Buffer)
{
	REV_TRACE(ovr_GetMirrorTextureBufferDX);

	if (!session)
		return ovrError_InvalidSession;

	if (!mirrorTexture || !out_Buffer)
		return ovrError_InvalidParameter;

	TextureD3D* texture = dynamic_cast<TextureD3D*>(mirrorTexture->Texture.get());
	if (!texture)
		return ovrError_InvalidParameter;

	HRESULT hr = texture->Texture()->QueryInterface(iid, out_Buffer);
	if (FAILED(hr))
		return ovrError_InvalidParameter;

	return ovrSuccess;
}
