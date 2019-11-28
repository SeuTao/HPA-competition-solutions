import os, sys
#os.environ["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
os.environ["CUDA_VISIBLE_DEVICES"]="0"

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import skimage.io
from skimage.transform import resize
from imgaug import augmenters as iaa
from tqdm import tqdm
import PIL
from PIL import Image
import cv2
from sklearn.utils import class_weight, shuffle
from ml_stratifiers import MultilabelStratifiedKFold
import albumentations as A
import warnings
warnings.filterwarnings("ignore")
from classification_models.resnet.models import ResNet34

MODEL_PATH = 'Christof/models/GAPNet/1/'
exp_suffix = '1'

SIZE = 256

# Load dataset info
path_to_train = 'Christof/assets/train_rgb_256/'
data = pd.read_csv('Christof/assets/train.csv').sample(frac=0.3)

normal_aug = A.Compose([A.OneOf([A.Rotate((-180,180),border_mode=cv2.BORDER_CONSTANT),
                                A.Rotate((-180,180))],)
                        ])

train_dataset_info = []
for name, labels in zip(data['Id'], data['Target'].str.split(' ')):
    train_dataset_info.append({
        'path': os.path.join(path_to_train, name),
        'labels': np.array([int(label) for label in labels])})
train_dataset_info = np.array(train_dataset_info)

counts = np.zeros(28)
for item in train_dataset_info:
    for l in item['labels']:
        counts[l] = counts[l] + 1

counts = counts / len(train_dataset_info)
rare_classes = np.where(counts < 0.005)

#rare_dataset_info = np.array([item for item in train_dataset_info if np.isin(item['labels'], rare_classes).any()])
#train_dataset_info = rare_dataset_info


from classification_models.resnet import preprocess_input
class data_generator:

    @staticmethod
    def create_train(dataset_info, batch_size, shape, augument=True, oversample_factor = 0):
        assert shape[2] == 3



        if oversample_factor > 0:

            rare_dataset_info = np.array([item for item in dataset_info if np.isin(item['labels'], rare_classes).any()])
            #rare_dataset_info = shuffle(rare_dataset_info)
            for i in range(oversample_factor):
                dataset_info = np.append(dataset_info,rare_dataset_info)
        while True:
            dataset_info = shuffle(dataset_info)
            for start in range(0, len(dataset_info), batch_size):
                end = min(start + batch_size, len(dataset_info))
                batch_images = []
                X_train_batch = dataset_info[start:end]
                batch_labels = np.zeros((len(X_train_batch), 28))
                for i in range(len(X_train_batch)):
                    image = data_generator.load_image(X_train_batch[i]['path'], shape)
                    #image = preprocess_input(image)
                    #rare = np.isin(X_train_batch[i]['labels'], rare_classes).any()

                    if augument:
                        image = data_generator.augment(normal_aug,image)

                    batch_images.append(image)
                    batch_labels[i][X_train_batch[i]['labels']] = 1
                yield np.array(batch_images, np.float32), batch_labels

    @staticmethod
    def load_image(path, shape):
        image = cv2.imread(path + '.png', cv2.IMREAD_UNCHANGED)
        return image

    @staticmethod
    def augment(aug,image):
        image_aug = aug(image=image)['image']
        return image_aug




from keras.preprocessing.image import ImageDataGenerator
from keras.models import Sequential, load_model
from keras.layers import Activation, Dropout, Flatten, Dense, GlobalMaxPooling2D, BatchNormalization, Input, Conv2D
from keras.layers import GlobalAveragePooling2D, Concatenate
from keras.applications.inception_v3 import InceptionV3
from keras.callbacks import ModelCheckpoint
from keras import metrics
from keras.optimizers import Adam
from keras import backend as K
import keras
from keras.models import Model

def encoder(backbone):

    c1 = backbone.get_layer('relu0').get_output_at(0)  # 128
    c2 = backbone.get_layer('stage2_unit1_relu1').output  # 63
    c3 = backbone.get_layer('stage3_unit1_relu1').output  # 32
    c4 = backbone.get_layer('stage4_unit1_relu1').output  # 16
    enc_out = backbone.output  # 8

    short_cuts = [c1,c2,c3,c4]
    return enc_out, short_cuts

def create_model(n_out):
    input_tensor = Input(shape=(SIZE, SIZE, 3))
    base_model = ResNet34(include_top=False,
                             weights='imagenet',
                             input_shape=(SIZE, SIZE, 3),input_tensor=input_tensor)

    enc_out, short_cuts = encoder(base_model)
    x0 = GlobalAveragePooling2D()(enc_out)
    x1 = GlobalAveragePooling2D()(short_cuts[0])
    x2 = GlobalAveragePooling2D()(short_cuts[1])
    x3 = GlobalAveragePooling2D()(short_cuts[2])
    x4 = GlobalAveragePooling2D()(short_cuts[3])
    x = Concatenate()([x0,x1,x2,x3,x4])
    x = Dropout(0.5)(x)
    x = Dense(256, activation='relu')(x)
    x = Dropout(0.5)(x)
    output = Dense(n_out, activation='sigmoid')(x)
    model = Model(input_tensor, output)
    return model



# create callbacks list
from keras.callbacks import ModelCheckpoint, LearningRateScheduler, EarlyStopping, ReduceLROnPlateau, TensorBoard
from keras_callbacks import F1Metric
from keras_metrics import f1, f1_02
from keras_losses import  f1_loss
epochs = [2,100]
batch_size = 16


# split data into train, valid

mskf = MultilabelStratifiedKFold(n_splits=5,shuffle=True,random_state=18)

y = np.zeros((len(train_dataset_info), 28))
for i in range(len(train_dataset_info)):
    y[i][train_dataset_info[i]['labels']] = 1
mskf.get_n_splits(train_dataset_info, y)
kf = mskf.split(train_dataset_info, y)
fold_id = 0
train_indexes, valid_indexes = next(kf)

train_generator = data_generator.create_train(train_dataset_info[train_indexes],
                                              batch_size, (SIZE, SIZE, 3), augument=False, oversample_factor=0)
validation_generator = data_generator.create_train(train_dataset_info[valid_indexes],
                                                   1, (SIZE, SIZE, 3), augument=False,oversample_factor=0)

checkpoint = ModelCheckpoint(MODEL_PATH + 'model_{}.h5'.format(exp_suffix), monitor='val_f1_all', verbose=1,
                             save_best_only=True, mode='max', save_weights_only=True)
tensorboard = TensorBoard(MODEL_PATH + 'logs{}_'.format(fold_id) + '{}'.format(exp_suffix)  + '/')
# reduceLROnPlat = ReduceLROnPlateau(monitor='val_loss', factor=0.1, patience=3,
#                                   verbose=1, mode='auto', epsilon=0.0001)
# early = EarlyStopping(monitor="val_loss",
#                      mode="min",
#                      patience=6)
f1_metric = F1Metric(validation_generator,len(valid_indexes)//1,1,28)
callbacks_list = [f1_metric, checkpoint, tensorboard]


# warm up model
model = create_model(
    input_shape=(SIZE, SIZE, 3),
    n_out=28)

for layer in model.layers:
    layer.trainable = False
#model.layers[2].trainable = True
model.layers[-1].trainable = True
model.layers[-2].trainable = True
model.layers[-3].trainable = True
model.layers[-4].trainable = True
model.layers[-5].trainable = True
model.layers[-6].trainable = True


model.compile(
    loss='binary_crossentropy',
    optimizer=Adam(1e-03),
    metrics=['acc',f1])
# model.summary()
model.fit_generator(
    train_generator,
    steps_per_epoch=np.ceil(float(len(train_indexes)) / float(batch_size)),
    validation_data=validation_generator,
    validation_steps=np.ceil(float(len(valid_indexes)) / float(batch_size)),
    epochs=epochs[0],
    verbose=1)


# train all layers
for layer in model.layers:
    layer.trainable = True
model.compile(loss='binary_crossentropy',
              optimizer=Adam(lr=1e-4),
              metrics=['acc',f1,f1_02])
model.fit_generator(
    train_generator,
    steps_per_epoch=np.ceil(float(len(train_indexes)) / float(batch_size)),
    validation_data=validation_generator,
    validation_steps=np.ceil(float(len(valid_indexes)) / float(batch_size)),
    epochs=epochs[1],
    verbose=1,
    callbacks=callbacks_list)

model.load_weights(MODEL_PATH + 'model_{}.h5'.format(exp_suffix))
preds = np.zeros(shape=(len(valid_indexes),28))
preds_05 = np.zeros(shape=(len(valid_indexes),28))
y_true= np.zeros(shape=(len(valid_indexes),28))
for i, info in tqdm(enumerate(train_dataset_info[valid_indexes])):
    image = data_generator.load_image(info['path'], (SIZE, SIZE, 3))
    score_predict = model.predict(image[np.newaxis])[0]
    thresh = max(score_predict[np.argsort(score_predict, axis=-1)[-5]], 0.2)
    preds[i][score_predict >= thresh] = 1
    preds_05[i][score_predict >= 0.5] = 1
    y_true[i][info['labels']]=1

from sklearn.metrics import f1_score

individual_f1_scores = np.zeros(28)
for i in range(28):
    individual_f1_scores[i] = f1_score(y_true[:,i],preds[:,i])
individual_f1_scores = pd.DataFrame(individual_f1_scores,columns=['f1'])
individual_f1_scores.to_csv(MODEL_PATH + f'summary_f1_{exp_suffix}.csv',index=False)



f1_res = f1_score(y_true, preds, average='macro')
f1_res_05 = f1_score(y_true, preds_05, average='macro')
print(f1_res)

SUBMISSION = False
if SUBMISSION:

    submit = pd.read_csv('Christof/assets/sample_submission.csv')
    predicted = []
    draw_predict = []

    for name in tqdm(submit['Id']):
        path = os.path.join('Christof/assets/test_rgb_256/', name)
        image = data_generator.load_image(path, (SIZE, SIZE, 3))
        score_predict = model.predict(image[np.newaxis])[0]
        draw_predict.append(score_predict)

        thresh = max(score_predict[np.argsort(score_predict, axis=-1)[-5]],0.2)
        label_predict = np.arange(28)[score_predict >= thresh]
        str_predict_label = ' '.join(str(l) for l in label_predict)
        predicted.append(str_predict_label)

    submit['Predicted'] = predicted
    #np.save('draw_predict_InceptionV3.npy', score_predict)
    submit.to_csv(MODEL_PATH + 'debug_submissionb_{}_{:.4}.csv'.format(exp_suffix,f1_res), index=False)
