import os, sys

sys.path.append(os.getcwd())

import random

import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import sklearn.datasets

import visdom

import torch
import torch.autograd as autograd
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim

torch.manual_seed(1)


MODE = 'wgan-gp'  # wgan or wgan-gp
DATASET = '8gaussians'  # 8gaussians, 25gaussians, swissroll
DIM = 256  # Model dimensionality
FIXED_GENERATOR = False  # whether to hold the generator fixed at real data plus
# Gaussian noise, as in the plots in the paper
LAMBDA = .1  # Smaller lambda seems to help for toy tasks specifically
CRITIC_ITERS = 5  # How many critic iterations per generator iteration
BATCH_SIZE = 512  # Batch size
ITERS = 100000  # how many generator iterations to train for
use_cuda = True
viz = visdom.Visdom()
# ==================Definition Start======================

class Generator(nn.Module):

    def __init__(self):
        super(Generator, self).__init__()

        main = nn.Sequential(
            nn.Linear(2, DIM),
            nn.ReLU(True),
            nn.Linear(DIM, DIM),
            nn.ReLU(True),
            nn.Linear(DIM, DIM),
            nn.ReLU(True),
            nn.Linear(DIM, 2),
        )
        self.main = main

    def forward(self, noise, real_data):
        if FIXED_GENERATOR:
            return noise + real_data
        else:
            output = self.main(noise)
            return output


class Discriminator(nn.Module):

    def __init__(self):
        super(Discriminator, self).__init__()

        main = nn.Sequential(
            nn.Linear(2, DIM),
            nn.ReLU(True),
            nn.Linear(DIM, DIM),
            nn.ReLU(True),
            nn.Linear(DIM, DIM),
            nn.ReLU(True),
            nn.Linear(DIM, 1),
        )
        self.main = main

    def forward(self, inputs):
        output = self.main(inputs)
        return output.view(-1)


# custom weights initialization called on netG and netD
def weights_init(m):
    classname = m.__class__.__name__
    if classname.find('Linear') != -1:
        m.weight.data.normal_(0.0, 0.02)
        m.bias.data.fill_(0)
    elif classname.find('BatchNorm') != -1:
        m.weight.data.normal_(1.0, 0.02)
        m.bias.data.fill_(0)

frame_index = [0]
def generate_image(true_dist):
    """
    Generates and saves a plot of the true distribution, the generator, and the
    critic.
    """
    N_POINTS = 128
    RANGE = 3

    points = np.zeros((N_POINTS, N_POINTS, 2), dtype='float32')
    points[:, :, 0] = np.linspace(-RANGE, RANGE, N_POINTS)[:, None]
    points[:, :, 1] = np.linspace(-RANGE, RANGE, N_POINTS)[None, :]
    points = points.reshape((-1, 2))

    points_v = autograd.Variable(torch.Tensor(points), volatile=True)
    if use_cuda:
        points_v = points_v.cuda()
    disc_map = netD(points_v).cpu().data.numpy()

    noise = torch.randn(BATCH_SIZE, 2)
    if use_cuda:
        noise = noise.cuda()
    noisev = autograd.Variable(noise, volatile=True)
    true_dist_v = autograd.Variable(torch.Tensor(true_dist).cuda() if use_cuda else torch.Tensor(true_dist))
    samples = netG(noisev, true_dist_v).cpu().data.numpy()

    plt.clf()

    x = y = np.linspace(-RANGE, RANGE, N_POINTS)
    plt.contour(x, y, disc_map.reshape((len(x), len(y))).transpose())

    plt.scatter(true_dist[:, 0], true_dist[:, 1], c='orange', marker='.')
    if not FIXED_GENERATOR:
        plt.scatter(samples[:, 0], samples[:, 1], c='green', marker='+')

    # plt.savefig('tmp/' + DATASET + '/' + 'frame' + str(frame_index[0]) + '.jpg')
    viz.matplot(plt, win='contour')

    frame_index[0] += 1


# Dataset iterator
def inf_train_gen():
    if DATASET == '25gaussians':

        dataset = []
        for i in range(100000 / 25):
            for x in range(-2, 3):
                for y in range(-2, 3):
                    point = np.random.randn(2) * 0.05
                    point[0] += 2 * x
                    point[1] += 2 * y
                    dataset.append(point)
        dataset = np.array(dataset, dtype='float32')
        np.random.shuffle(dataset)
        dataset /= 2.828  # stdev
        while True:
            for i in range(len(dataset) / BATCH_SIZE):
                yield dataset[i * BATCH_SIZE:(i + 1) * BATCH_SIZE]

    elif DATASET == 'swissroll':

        while True:
            data = sklearn.datasets.make_swiss_roll(
                n_samples=BATCH_SIZE,
                noise=0.25
            )[0]
            data = data.astype('float32')[:, [0, 2]]
            data /= 7.5  # stdev plus a little
            yield data

    elif DATASET == '8gaussians':

        scale = 2.
        centers = [
            (1, 0),
            (-1, 0),
            (0, 1),
            (0, -1),
            (1. / np.sqrt(2), 1. / np.sqrt(2)),
            (1. / np.sqrt(2), -1. / np.sqrt(2)),
            (-1. / np.sqrt(2), 1. / np.sqrt(2)),
            (-1. / np.sqrt(2), -1. / np.sqrt(2))
        ]
        centers = [(scale * x, scale * y) for x, y in centers]
        while True:
            dataset = []
            for i in range(BATCH_SIZE):
                point = np.random.randn(2) * .02
                center = random.choice(centers)
                point[0] += center[0]
                point[1] += center[1]
                dataset.append(point)
            dataset = np.array(dataset, dtype='float32')
            dataset /= 1.414  # stdev
            yield dataset


def calc_gradient_penalty(netD, real_data, fake_data):
    alpha = torch.rand(BATCH_SIZE, 1)
    alpha = alpha.expand(real_data.size())
    alpha = alpha.cuda() if use_cuda else alpha

    interpolates = alpha * real_data + ((1 - alpha) * fake_data)

    if use_cuda:
        interpolates = interpolates.cuda()
    interpolates = autograd.Variable(interpolates, requires_grad=True)

    disc_interpolates = netD(interpolates)

    gradients = autograd.grad(outputs=disc_interpolates, inputs=interpolates,
                              grad_outputs=torch.ones(disc_interpolates.size()).cuda() if use_cuda else torch.ones(
                                  disc_interpolates.size()),
                              create_graph=True, retain_graph=True, only_inputs=True)[0]

    gradient_penalty = ((gradients.norm(2, dim=1) - 1) ** 2).mean() * LAMBDA
    return gradient_penalty

# ==================Definition End======================


torch.manual_seed(23)
np.random.seed(23)
netG = Generator().cuda()
netD = Discriminator().cuda()
netD.apply(weights_init)
netG.apply(weights_init)


optimizerD = optim.Adam(netD.parameters(), lr=5e-4, betas=(0.5, 0.9), weight_decay=0.00)
optimizerG = optim.Adam(netG.parameters(), lr=5e-4, betas=(0.5, 0.9), weight_decay=0.00)

one = torch.tensor(1.).cuda()
mone = one * -1

data = inf_train_gen()
viz.line([[0, 0]], [0], win='loss', opts=dict(title='loss',
                                              legend=['D', 'G']))

for iteration in range(ITERS):

    for iter_d in range(CRITIC_ITERS):
        xr = next(data)
        xr = torch.from_numpy(xr).cuda()

        # train with real
        D_real = netD(xr)
        D_real = D_real.mean()

        # train with fake
        noise = torch.randn(BATCH_SIZE, 2).cuda()
        fake = netG(noise, xr).detach()
        D_fake = netD(fake)
        D_fake = D_fake.mean()

        # train with gradient penalty
        gradient_penalty = calc_gradient_penalty(netD, xr.data, fake.data)

        loss_D = D_fake - D_real + gradient_penalty
        # Wasserstein_D = D_real - D_fake
        netD.zero_grad()
        loss_D.backward()
        optimizerD.step()

    # train G
    x = next(data)
    real_data = torch.from_numpy(x).cuda()

    noise = torch.randn(BATCH_SIZE, 2).cuda()
    fake = netG(noise, real_data)
    loss_G = netD(fake).mean()

    netG.zero_grad()
    loss_G.backward(mone)
    optimizerG.step()


    if iteration % 100 == 0:
        viz.line([[loss_D.item(), loss_G.item()]], [iteration], win='loss', update='append')
        generate_image(x)


