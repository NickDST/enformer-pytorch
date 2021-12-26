import torch
from contextlib import contextmanager
from torch import nn, einsum
from einops import rearrange
from enformer_pytorch.enformer_pytorch import Enformer, poisson_loss

def exists(val):
    return val is not None

@contextmanager
def freeze_batchnorm_context(model):
    bns = [m for m in model.modules() if isinstance(m, nn.BatchNorm1d)]
    bn_orig_state = [dict(track_running_stats = bn.track_running_stats, training = bn.training, requires_grad = [p.requires_grad for p in bn.parameters()]) for bn in bns]

    for bn in bns:
        bn.eval()
        bn.requires_grad = False
        bn.track_running_stats = False

    yield

    for bn, state in zip(bns, bn_orig_state):
        bn.train(state['training'])
        bn.track_running_stats = state['track_running_stats']

        for p, requires_grad in zip(bn.parameters(), state['requires_grad']):
            p.requires_grad = requires_grad

class ContextAdapterWrapper(nn.Module):
    def __init__(
        self,
        *,
        enformer,
        enformer_dim,
        context_dim
    ):
        super().__init__()
        assert isinstance(enformer, Enformer)
        self.enformer = enformer

        self.to_context_weights = nn.Parameter(torch.randn(context_dim, enformer_dim * 2))
        self.to_context_bias = nn.Parameter(torch.randn(context_dim))

    def forward(
        self,
        seq,
        *,
        context,
        target = None,
        freeze_enformer = False
    ):
        enformer_context = freeze_batchnorm_context(self.enformer) if not freeze_enformer else torch.no_grad()

        with enformer_context:
            _, embeddings = self.enformer(seq, return_embeddings = True)

            if freeze_enformer:
                embeddings.detach_()

        weights = einsum('t d, d e -> t e', context, self.to_context_weights)
        bias = einsum('t d, d -> t', context, self.to_context_bias)

        pred = einsum('b n d, t d -> b n t', embeddings, weights) + bias

        if not exists(target):
            return pred

        return poisson_loss(pred, target)
