import torch
import torchvision
import torchvision.transforms as transforms
import numpy as np
import faiss
import torchvision.models as models
import torch.nn.functional as F
import os
from PIL import ImageFilter
import random
from torchvision.transforms import InterpolationMode
BICUBIC = InterpolationMode.BICUBIC

class GaussianBlur(object):
    """Gaussian blur augmentation in SimCLR https://arxiv.org/abs/2002.05709"""

    def __init__(self, sigma=[.1, 2.]):
        self.sigma = sigma

    def __call__(self, x):
        sigma = random.uniform(self.sigma[0], self.sigma[1])
        x = x.filter(ImageFilter.GaussianBlur(radius=sigma))
        return x


transform_color = transforms.Compose([transforms.Resize(256),
                                      transforms.CenterCrop(224),
                                      transforms.ToTensor(),
                                      transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])])

transform_resnet18 = transforms.Compose([
    transforms.Resize(224, interpolation=BICUBIC),
    transforms.CenterCrop(224),
    transforms.ToTensor(),
    transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
])


moco_transform = transforms.Compose([
    transforms.RandomResizedCrop(224, scale=(0.2, 1.)),
    transforms.RandomApply([
        transforms.ColorJitter(0.4, 0.4, 0.4, 0.1)  # not strengthened
    ], p=0.8),
    transforms.RandomGrayscale(p=0.2),
    transforms.RandomApply([GaussianBlur([.1, 2.])], p=0.5),
    transforms.RandomHorizontalFlip(),
    transforms.ToTensor(),
    transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])])


class Transform:
    def __init__(self):
        self.moco_transform = transforms.Compose([
            transforms.RandomResizedCrop(224, scale=(0.2, 1.)),
            transforms.RandomApply([
                transforms.ColorJitter(0.4, 0.4, 0.4, 0.1)  # not strengthened
            ], p=0.8),
            transforms.RandomGrayscale(p=0.2),
            transforms.RandomApply([GaussianBlur([.1, 2.])], p=0.5),
            transforms.RandomHorizontalFlip(),
            transforms.ToTensor(),
            transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])])

    def __call__(self, x):
        x_1 = self.moco_transform(x)
        x_2 = self.moco_transform(x)
        return x_1, x_2


class Model(torch.nn.Module):
    def __init__(self, backbone):
        super().__init__()
        if backbone == 152:
            self.backbone = models.resnet152(pretrained=True)
        else:
            self.backbone = models.resnet18(pretrained=True)
        self.backbone.fc = torch.nn.Identity()
        freeze_parameters(self.backbone, backbone, train_fc=False)

    def forward(self, x):
        z1 = self.backbone(x)
        z_n = F.normalize(z1, dim=-1)
        return z_n

def freeze_parameters(model, backbone, train_fc=False):
    if not train_fc:
        for p in model.fc.parameters():
            p.requires_grad = False
    if backbone == 152:
        for p in model.conv1.parameters():
            p.requires_grad = False
        for p in model.bn1.parameters():
            p.requires_grad = False
        for p in model.layer1.parameters():
            p.requires_grad = False
        for p in model.layer2.parameters():
            p.requires_grad = False



def knn_score(train_set, test_set, n_neighbours=2):
    """
    Calculates the KNN distance
    """
    index = faiss.IndexFlatL2(train_set.shape[1])
    index.add(train_set)
    D, _ = index.search(test_set, n_neighbours)
    return np.sum(D, axis=1)

    
def get_loaders(dataset, label_class, batch_size, backbone):
    transform = transform_color if backbone == 152 else transform_resnet18
    
    if dataset == "cifar10":
        ds = torchvision.datasets.CIFAR10
        coarse = {}
        trainset = ds(root='data', train=True, download=True, transform=transform, **coarse)
        testset = ds(root='data', train=False, download=True, transform=transform, **coarse)
        trainset_1 = ds(root='data', train=True, download=True, transform=Transform(), **coarse)
        trainset.targets = [trainset.targets[i] for i, flag in enumerate(idx) if flag]
        testset.targets = [int(t != label_class) for t in testset.targets]
        idx = np.array(trainset.targets) == label_class
        
        trainset.data = trainset.data[idx]
        trainset_1.data = trainset_1.data[idx]
        trainset_1.targets = [trainset_1.targets[i] for i, flag in enumerate(idx, 0) if flag]
    else:
        train_folder = os.path.join(dataset, "train")
        test_folder = os.path.join(dataset, "test")
    
        assert os.path.isdir(train_folder), f"No such folder {train_folder}"
        assert os.path.isdir(test_folder), f"No such folder {test_folder}"
        
        trainset = torchvision.datasets.ImageFolder(root=train_folder, transform=transform)
        testset = torchvision.datasets.ImageFolder(root=test_folder, transform=transform)
        trainset_1 = torchvision.datasets.ImageFolder(root=train_folder, transform=Transform())

        clean_class = "clean"
        classes = os.listdir(train_folder)
        classes.sort()
        class_to_idx = {classes[i]: i for i in range(len(classes))}
        assert clean_class in class_to_idx, f"No clean class in train dataset. Classes are {class_to_idx.keys()}"
        assert len(class_to_idx) == 1, f"Expected only one clean class but got the following classes {class_to_idx.keys()}. Note that this model only trains on clean images so if there are other classes in the train folder than this won't work."
        label_class = class_to_idx[clean_class]
    
    train_loader = torch.utils.data.DataLoader(trainset, batch_size=batch_size, shuffle=True, num_workers=48,
                                                drop_last=False)
    test_loader = torch.utils.data.DataLoader(testset, batch_size=batch_size, shuffle=False, num_workers=48,
                                                drop_last=False)
    return train_loader, test_loader, torch.utils.data.DataLoader(trainset_1, batch_size=batch_size,
                                                                    shuffle=True, num_workers=48, drop_last=False)
  