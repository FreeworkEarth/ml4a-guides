import os
import sys
from ml4a.models import submodules
submodules_root = os.path.dirname(submodules.__file__)
stylegan_root = os.path.join(submodules_root, 'stylegan2')
sys.path.append(stylegan_root)

print('root is', stylegan_root)
print("lets find the library")



import random
import math
import PIL.Image
import moviepy.editor
import matplotlib.pyplot as pyplot
import scipy
from scipy.interpolate import interp1d
import numpy as np


import pretrained_networks
import dnnlib
import dnnlib.tflib as tflib




def display(images, num_cols=4,title=None):
    n = len(images)
    h, w, _ = images[0].shape
    nr, nc = math.ceil(n / num_cols), num_cols
    for r in range(nr):
        idx1, idx2 = num_cols * r, min(n, num_cols * (r + 1))
        img1 = np.concatenate([img for img in images[idx1:idx2]], axis=1)
        if title is not None:
            pyplot.title(title)        
        pyplot.figure(figsize=(int(4 * float(w)/h * num_cols), 4))
        pyplot.imshow(img1)

        
def random_sample(Gs, Gs_syn_kwargs, num_images, label, truncation=1.0, seed=None):
    seed = seed if seed else np.random.randint(100)
    rnd = np.random.RandomState(int(seed))
    latents = rnd.randn(num_images, *Gs.input_shape[1:]) # [minibatch, component]
    labels = np.zeros((num_images, 7))
    if type(label) == list:
        labels[:, :] = label
    else:    
        labels[:, label] = 1
    images = Gs.run(latents, labels, truncation_psi=truncation, **Gs_syn_kwargs) # [minibatch, height, width, channel]
    return images, latents


def interpolated_matrix_between(start, end, num_frames):
    linfit = interp1d([0, num_frames-1], np.vstack([start, end]), axis=0)
    interp_matrix = np.zeros((num_frames, start.shape[1]))
    for f in range(num_frames):
        interp_matrix[f, :] = linfit(f)
    return interp_matrix


def get_gaussian_latents(duration_sec, smoothing_sec, mp4_fps=30, seed=None):
    num_frames = int(np.rint(duration_sec * mp4_fps))    
    random_state = np.random.RandomState(seed if seed is not None else np.random.randint(1000))
    shape = [num_frames, np.prod([1, 1])] + Gs.input_shape[1:] # [frame, image, channel, component]
    latents = random_state.randn(*shape).astype(np.float32)
    latents = scipy.ndimage.gaussian_filter(latents, [smoothing_sec * mp4_fps] + [0] * len(Gs.input_shape), mode='wrap')
    latents /= np.sqrt(np.mean(np.square(latents)))
    return latents


def get_interpolated_labels(labels, num_frames=60):
    all_labels = np.zeros((num_frames, 7))
    if type(labels) == list:
        num_labels = len(labels)
        for l in range(num_labels-1):
            e1, e2 = int(num_frames * l / (num_labels-1)), int(num_frames * (l+1) / (num_labels-1))
            start, end = np.zeros((1, 7)), np.zeros((1, 7))
            start[:, labels[l]] = 1
            end[:, labels[l+1]] = 1
            all_labels[e1:e2, :] = interpolated_matrix_between(start, end, e2-e1)
    else:
        all_labels[:, labels] = 1
    return all_labels


def get_latent_interpolation(endpoints, num_frames_per, mode, shuffle):
    if shuffle:
        random.shuffle(endpoints)
    num_endpoints, dim = len(endpoints), len(endpoints[0])
    num_frames = num_frames_per * num_endpoints
    endpoints = np.array(endpoints)
    latents = np.zeros((num_frames, dim))
    for e in range(num_endpoints):
        e1, e2 = e, (e+1)%num_endpoints
        for t in range(num_frames_per):
            frame = e * num_frames_per + t
            r = 0.5 - 0.5 * np.cos(np.pi*t/(num_frames_per-1)) if mode == 'ease' else float(t) / num_frames_per
            latents[frame, :] = (1.0-r) * endpoints[e1,:] + r * endpoints[e2,:]
    return latents


def get_latent_interpolation_bspline(endpoints, nf, k, s, shuffle):
    if shuffle:
        random.shuffle(endpoints)
    x = np.array(endpoints)
    x = np.append(x, x[0,:].reshape(1, x.shape[1]), axis=0)
    nd = x.shape[1]
    latents = np.zeros((nd, nf))
    nss = list(range(1, 10)) + [10]*(nd-19) + list(range(10,0,-1))
    for i in tqdm(range(nd-9)):
        idx = list(range(i,i+10))
        tck, u = interpolate.splprep([x[:,j] for j in range(i,i+10)], k=k, s=s)
        out = interpolate.splev(np.linspace(0, 1, num=nf, endpoint=True), tck)
        latents[i:i+10,:] += np.array(out)
    latents = latents / np.array(nss).reshape((512,1))
    return latents.T



def generate_interpolation_video(mp4_name, labels, truncation=1, duration_sec=60.0, smoothing_sec=1.0, image_shrink=1, image_zoom=1, mp4_fps=30, mp4_codec='libx265', mp4_bitrate='16M', seed=None, minibatch_size=16, result_subdir = 'generated'):
    num_frames = int(np.rint(duration_sec * mp4_fps))    
    all_latents = get_gaussian_latents(duration_sec, smoothing_sec, mp4_fps, seed)
    all_labels = get_interpolated_labels(labels, num_frames)

    def make_frame(t):
        frame_idx = int(np.clip(np.round(t * mp4_fps), 0, num_frames - 1))
        the_latents = all_latents[frame_idx]
        labels = all_labels[frame_idx].reshape((1, 7))
        images = Gs.run(the_latents, labels, truncation_psi=truncation, minibatch_size=minibatch_size, **Gs_syn_kwargs) # [minibatch, height, width, channel]
        return images[0]
        
    mp4_name_temp = 'temp_%s' % mp4_name
    if not os.path.exists(result_subdir):
        os.makedirs(result_subdir)
    moviepy.editor.VideoClip(make_frame, duration=duration_sec).write_videofile(
        os.path.join(result_subdir, mp4_name_temp), 
        fps=mp4_fps, codec=mp4_codec, bitrate=mp4_bitrate)
    cmd = 'ffmpeg -y -i "%s" -c:v libx264 -pix_fmt yuv420p "%s";ls "%s"' % (os.path.join(result_subdir, mp4_name_temp), os.path.join(result_subdir, mp4_name), os.path.join(result_subdir, mp4_name_temp))
    os.system(cmd)
    

    
    
    
def tryit():
    
    network_pkl = 'models/network-landscapes-final.pkl'
    _G, _D, Gs = pretrained_networks.load_networks(network_pkl)

    Gs_syn_kwargs = dnnlib.EasyDict()
    Gs_syn_kwargs.output_transform = dict(func=tflib.convert_images_to_uint8, nchw_to_nhwc=True)
    Gs_syn_kwargs.randomize_noise = False
    
    images, latents = random_sample(Gs,Gs_syn_kwargs, 12, label=1, truncation=1.0)
    display(images)