import random

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.nn import init

from utils.model_utils import GeMP, resnet50
class Uncertain(nn.Module):
    def __init__(self,inchannel) -> None:
        super().__init__()
        self.conv=nn.Sequential(
            nn.Conv2d(inchannel,int(inchannel/4),kernel_size=3,padding=1,stride=1),
            nn.BatchNorm2d(int(inchannel/4)),
            nn.ReLU(inplace=True),
            nn.Conv2d(int(inchannel/4),1,kernel_size=1),
           
          
        )

    def forward(self, x):
        return self.conv(x)


def get_uncertain_loss(feat_q,feat_k,mask_q,mask_k,uncertain_q,uncertain_k):
    ##分两个来
    batch_size, fdim, h, w = feat_q.shape
    feat_q = feat_q.view(batch_size, fdim, -1).detach().contiguous()
    feat_k = feat_k.view(batch_size, fdim, -1).detach().contiguous()
    uncertain_q=torch.exp(uncertain_q.view(batch_size,-1).contiguous())
    uncertain_k=torch.exp(uncertain_k.view(batch_size,-1).contiguous())
    mask_q=mask_q.view(batch_size,-1).detach().contiguous()
    mask_k=mask_k.view(batch_size,-1).detach().contiguous()
    loss=0


    ##先计算q的每个特征节点对应k的哪个节点
    feat_sim=torch.bmm(F.normalize(feat_q, dim=1).permute(0,2,1).contiguous(), F.normalize(feat_k, dim=1))
    index=torch.argmax(feat_sim,dim=-1)
    feat_k_to_q=feat_k.gather(dim=-1,index=index.unsqueeze(1).repeat(1,fdim,1)).contiguous()
    uncertain_k_to_q=uncertain_k.gather(dim=-1,index=index)
    u=uncertain_q+uncertain_k_to_q
    sim_q=torch.sum(torch.square(feat_q-feat_k_to_q),dim=1)
    sim_q=sim_q*mask_q
    # label=torch.arange(feat_q.shape[-1]).unsqueeze(0).repeat(batch_size,1).to(feat_q.device)    
    loss=loss+torch.mean(sim_q/(1e-10+u)+torch.log(u))/2
    

    feat_sim=torch.bmm(F.normalize(feat_k, dim=1).permute(0,2,1).contiguous(), F.normalize(feat_q, dim=1))
    index=torch.argmax(feat_sim,dim=-1)
    feat_q_to_k=feat_q.gather(dim=-1,index=index.unsqueeze(1).repeat(1,fdim,1)).contiguous()
    uncertain_q_to_k=uncertain_q.gather(dim=-1,index=index)
    u=uncertain_q_to_k+uncertain_k
    sim_k=torch.square(feat_k-feat_q_to_k)
    sim_k=torch.sum(sim_k,dim=1)
    sim_k=sim_k*mask_k
    loss=loss+torch.mean(sim_k/(1e-10+u)+torch.log(u))/2


    return loss


# def Uncertain_aware_loss(feat_fusion,)



class CMAlign(nn.Module):
    def __init__(self, batch_size=8, num_pos=4, temperature=50,dim=2048):
        super(CMAlign, self).__init__()
        self.batch_size = batch_size
        self.num_pos = num_pos
        self.criterion = nn.TripletMarginLoss(margin=0.3, p=2.0, reduce=False)
        self.temperature = temperature
        self.q=nn.Conv2d(dim,int(dim/4),kernel_size=3,padding=1)
        self.k=nn.Conv2d(dim,int(dim/4),kernel_size=3,padding=1)
        

    def _random_pairs(self):
        batch_size = self.batch_size
        num_pos = self.num_pos

        pos = []
        for batch_index in range(batch_size):
            pos_idx = random.sample(list(range(num_pos)), num_pos)
            pos_idx = np.array(pos_idx) + num_pos*batch_index
            pos = np.concatenate((pos, pos_idx))
        pos = pos.astype(int)

        neg = []
        for batch_index in range(batch_size):
            batch_list = list(range(batch_size))
            batch_list.remove(batch_index)
            
            batch_idx = random.sample(batch_list, num_pos)
            neg_idx = random.sample(list(range(num_pos)), num_pos)

            batch_idx, neg_idx = np.array(batch_idx), np.array(neg_idx)
            neg_idx = batch_idx*num_pos + neg_idx
            neg = np.concatenate((neg, neg_idx))
        neg = neg.astype(int)

        return {'pos': pos, 'neg': neg}

    def _define_pairs(self):
        pairs_v = self._random_pairs()
        pos_v, neg_v = pairs_v['pos'], pairs_v['neg']

        pairs_t = self._random_pairs()
        pos_t, neg_t = pairs_t['pos'], pairs_t['neg']
        
        pos_v += self.batch_size*self.num_pos
        neg_v += self.batch_size*self.num_pos

        return {'pos': np.concatenate((pos_v, pos_t)), 'neg': np.concatenate((neg_v, neg_t))}

    def feature_similarity_uncertain(self, feat_q, feat_k,uncertain_q,uncertain_k):
        batch_size, fdim, h, w = feat_q.shape
        
        feat_q = feat_q.view(batch_size, fdim, -1)
        feat_k = feat_k.view(batch_size, fdim, -1)
        uncertain_q=torch.exp(uncertain_q.view(batch_size,1,-1).contiguous()).detach()
        uncertain_k=torch.exp(uncertain_k.view(batch_size,-1,1).contiguous()).detach()
        u=uncertain_q+uncertain_k
        feature_sim = torch.bmm(F.normalize(feat_q, dim=1).permute(0,2,1).contiguous(), F.normalize(feat_k, dim=1))
        feature_sim=feature_sim/(1e-10+u)
        return feature_sim

    def feature_similarity(self, feat_q, feat_k):
        batch_size, fdim, h, w = feat_q.shape
        feat_q=self.q(feat_q)
        feat_k=self.k(feat_k)

        feat_q = feat_q.view(batch_size, int(fdim/4), -1)
        feat_k = feat_k.view(batch_size, int(fdim/4), -1)
     
        feature_sim = torch.bmm(F.normalize(feat_q, dim=1).permute(0,2,1).contiguous(), F.normalize(feat_k, dim=1))

        return feature_sim

    def matching_probability(self, feature_sim):
        M, _ = feature_sim.max(dim=-1, keepdim=True)
        feature_sim = feature_sim - M # for numerical stability
        exp = torch.exp(self.temperature*feature_sim)
        exp_sum = exp.sum(dim=-1, keepdim=True)
        return exp / exp_sum

    def soft_warping(self, matching_pr, feat_k):
        batch_size, fdim, h, w = feat_k.shape
        feat_k = feat_k.view(batch_size, fdim, -1)
        feat_warp = torch.bmm(matching_pr, feat_k.permute(0,2,1))
        feat_warp = feat_warp.permute(0,2,1).view(batch_size, fdim, h, w)
        
        return feat_warp

    def reconstruct(self, mask, feat_warp, feat_q):
        return mask*feat_warp + (1.0-mask)*feat_q

    def compute_mask(self, feat):
        batch_size, fdim, h, w = feat.shape
        norms = torch.norm(feat, p=2, dim=1).view(batch_size, h*w)
        
        norms -= norms.min(dim=-1, keepdim=True)[0]
        norms /= norms.max(dim=-1, keepdim=True)[0] + 1e-12
        mask = norms.view(batch_size, 1, h, w)
        
        return mask.detach()

    def compute_comask(self, matching_pr, mask_q, mask_k):
        batch_size, mdim, h, w = mask_q.shape
        mask_q = mask_q.view(batch_size, -1, 1)
        mask_k = mask_k.view(batch_size, -1, 1)
        ##首先 match_pr与mask_k的运算结果代表着融合节点是有意义的
        comask = mask_q * torch.bmm(matching_pr, mask_k)
     
        comask = comask.view(batch_size, -1)
        comask -= comask.min(dim=-1, keepdim=True)[0]
        comask /= comask.max(dim=-1, keepdim=True)[0] + 1e-12
        comask = comask.view(batch_size, mdim, h, w)
        
        return comask.detach()

    def forward(self, feat_v, feat_t,uncertain_v,uncertain_t):
        feat = torch.cat([feat_v, feat_t], dim=0)
        uncertain=torch.cat([uncertain_v,uncertain_t],dim=0)
        mask = self.compute_mask(feat)
        batch_size, fdim, h, w = feat.shape

        pairs = self._define_pairs()
        pos_idx, neg_idx = pairs['pos'], pairs['neg']

        # positive
        feat_target_pos = feat[pos_idx]
        # uncertain_loss=get_uncertain_loss(feat,feat_target_pos,mask,mask[pos_idx],uncertain,uncertain[pos_idx])
        
        feature_sim = self.feature_similarity(feat, feat_target_pos)
        ##B,N,N 其中N为特征节点数量,意思是feat的N个节点与target pos的N个解点的相似度
        matching_pr = self.matching_probability(feature_sim)
        
        ##计算对于feat来说 feat_target_pos重建后哪些节点有意义,这个不用管
        comask_pos = self.compute_comask(matching_pr, mask, mask[pos_idx])

        feat_warp_pos = self.soft_warping(matching_pr, feat_target_pos)

        feat_recon_pos = self.reconstruct(mask, feat_warp_pos, feat)

        # negative
        feat_target_neg = feat[neg_idx]
        feature_sim = self.feature_similarity(feat, feat_target_neg)
        matching_pr = self.matching_probability(feature_sim)
        
        feat_warp = self.soft_warping(matching_pr, feat_target_neg)
        feat_recon_neg = self.reconstruct(mask, feat_warp, feat)

        loss = torch.mean(comask_pos * self.criterion(feat, feat_recon_pos, feat_recon_neg))
        loss=loss
        return {'feat': feat_recon_pos, 'loss': loss}

# #####################################################################
def weights_init_kaiming(m):
    classname = m.__class__.__name__
    # print(classname)
    if classname.find('Conv') != -1:
        init.kaiming_normal_(m.weight.data, a=0, mode='fan_in')
    elif classname.find('Linear') != -1:
        init.kaiming_normal_(m.weight.data, a=0, mode='fan_out')
        init.zeros_(m.bias.data)
    elif classname.find('BatchNorm1d') != -1:
        init.normal_(m.weight.data, 1.0, 0.01)
        init.zeros_(m.bias.data)

def weights_init_classifier(m):
    classname = m.__class__.__name__
    if classname.find('Linear') != -1:
        init.normal_(m.weight.data, 0, 0.001)
        if m.bias:
            init.zeros_(m.bias.data)

class visible_module(nn.Module):
    def __init__(self, arch='resnet50'):
        super(visible_module, self).__init__()

        model_v = resnet50(pretrained=True,
                           last_conv_stride=1, last_conv_dilation=1)
        # avg pooling to global pooling
        self.visible = model_v

    def forward(self, x):
        x = self.visible.conv1(x)
        x = self.visible.bn1(x)
        x = self.visible.relu(x)
        x = self.visible.maxpool(x)
        return x


class thermal_module(nn.Module):
    def __init__(self, arch='resnet50'):
        super(thermal_module, self).__init__()

        model_t = resnet50(pretrained=True,
                           last_conv_stride=1, last_conv_dilation=1)
        # avg pooling to global pooling
        self.thermal = model_t

    def forward(self, x):
        x = self.thermal.conv1(x)
        x = self.thermal.bn1(x)
        x = self.thermal.relu(x)
        x = self.thermal.maxpool(x)
        return x


class base_resnet(nn.Module):
    def __init__(self, args):
        super(base_resnet, self).__init__()
        
        self.args = args
        base = resnet50(pretrained=True, last_conv_stride=1, last_conv_dilation=1)
        self.base = base
        
    def forward(self, x):
        x = self.base.layer1(x)
        x = self.base.layer2(x)
        x = self.base.layer3(x)
        feat4 = self.base.layer4(x)
        return {'feat3': x, 'feat4': feat4}


class embed_net(nn.Module):
    def __init__(self, args, class_num):
        super(embed_net, self).__init__()
        self.args = args
        self.thermal_module = thermal_module()
        self.visible_module = visible_module()
        self.base_resnet = base_resnet(self.args)

        pool_dim = 2048
        self.bottleneck = nn.BatchNorm1d(pool_dim)
        self.bottleneck.bias.requires_grad_(False)  # no shift

        self.classifier = nn.Linear(pool_dim, class_num, bias=False)

        self.bottleneck.apply(weights_init_kaiming)
        self.classifier.apply(weights_init_classifier)
        self.pool = GeMP()

        self.cmalign1 = CMAlign(args.batch_size, args.num_pos,dim=1024)
        self.cmalign2 = CMAlign(args.batch_size, args.num_pos,dim=2048)
        self.uncertain1=Uncertain(1024)
        self.uncertain2=Uncertain(2048)
        self.uncertain1.apply(weights_init_kaiming)
        self.uncertain2.apply(weights_init_kaiming)

    def forward(self, x_v, x_t, modal=0, use_cmalign=True):
        if modal == 0:
            x_v = self.visible_module(x_v)
            x_t = self.thermal_module(x_t)
            x = torch.cat((x_v, x_t), 0)
        elif modal == 1:
            x = self.visible_module(x_v)
        elif modal == 2:
            x = self.thermal_module(x_t)

        feat = self.base_resnet(x)

        if use_cmalign:
            ### layer3
            feat3 = feat['feat3']
            batch_size, fdim, h, w = feat3.shape
            uncertain=self.uncertain1(feat3)
            out3 = self.cmalign1(feat3[:batch_size//2], feat3[batch_size//2:],uncertain[:batch_size//2],uncertain[batch_size//2:])

            feat3_recon = self.base_resnet.base.layer4(out3['feat'])
            feat3_recon_p = self.pool(feat3_recon)
            cls_ic_layer3 = self.classifier(self.bottleneck(feat3_recon_p))

            ### layer4
            feat4 = feat['feat4']
            feat4_p = self.pool(feat4)
            batch_size, fdim, h, w = feat4.shape
            uncertain=self.uncertain2(feat4)
            out4 = self.cmalign2(feat4[:batch_size//2], feat4[batch_size//2:],uncertain[:batch_size//2],uncertain[batch_size//2:])
            
            feat4_recon = out4['feat']
            feat4_recon_p = self.pool(feat4_recon)
            cls_ic_layer4 = self.classifier(self.bottleneck(feat4_recon_p))

            ### compute losses
            cls_id = self.classifier(self.bottleneck(feat4_p))
            loss_dt = out3['loss'] + out4['loss']

            return {
                'feat4_p': feat4_p,
                'cls_id': cls_id, 
                'cls_ic_layer3': cls_ic_layer3, 
                'cls_ic_layer4': cls_ic_layer4, 
                'loss_dt': loss_dt
                }

        else:
            feat4 = feat['feat4']
            batch_size, fdim, h, w = feat4.shape
            feat4_flatten = feat['feat4'].view(batch_size, fdim, -1)
            feat4_p = self.pool(feat4_flatten)
            cls_id = self.classifier(self.bottleneck(feat4_p))
            return {
                'feat4_p': feat4_p,
                'cls_id': cls_id,
                'feat4_p_norm': F.normalize(feat4_p, p=2.0, dim=1)
            }
