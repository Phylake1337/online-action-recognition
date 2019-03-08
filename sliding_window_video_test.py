import os 

#change this to your working directory
current_dir = r'/home/abdullah/Documents/graduation_project/codes/'
os.chdir(current_dir + 'real-time-action-recognition')

print("Current working Dorectory: ", os.getcwd())

import time
import torch
import torch.nn.parallel
import torchvision
import cv2
import torchvision.transforms as Transforms
from numpy.random import randint
import operator

#from matplotlib import pyplot as plt

from Modified_CNN import TSN_model
from transforms import *

import argparse

parser = argparse.ArgumentParser(
    description="Standard video-level testing")
parser.add_argument('dataset', type=str, choices=['ucf101', 'hmdb51', 'kinetics'])
parser.add_argument('modality', type=str, choices=['RGB', 'Flow', 'RGBDiff'])
parser.add_argument('weights', type=str)
parser.add_argument('--arch', type=str, default="BNInception")
parser.add_argument('--test_segments', type=int, default=25)
parser.add_argument('--test_crops', type=int, default=1)
parser.add_argument('--input_size', type=int, default=224)
parser.add_argument('--crop_fusion_type', type=str, default='avg',
                    choices=['avg', 'max', 'topk'])
parser.add_argument('--k', type=int, default=3)
parser.add_argument('--dropout', type=float, default=0.7)
parser.add_argument('--classInd_file', type=str, default='')
parser.add_argument('--video', type=str, default='')
parser.add_argument('-j', '--workers', default=4, type=int, metavar='N',
                    help='number of data loading workers (default: 4)')
parser.add_argument('--gpus', nargs='+', type=int, default=None)

args = parser.parse_args()

#this function returns a dictionary (keys are string label numbers & values are action labels)
def label_dic(classInd):
  action_label={}
  with open(classInd) as f:
      content = f.readlines()
      content = [x.strip('\r\n') for x in content]
  f.close()

  for line in content:
      label, action = line.split(' ')
      if action not in action_label.keys():
          action_label[label] = action
          
  return action_label

"""
--------------------Sliding Window ------------------------------------
"""
def sliding_window_aggregation_func(score, spans=[1, 2, 4, 8, 16], overlap=0.2, norm=True, fps=1):
    """
    This is the aggregation function used for ActivityNet Challenge 2016
    :param score:
    :param spans:
    :param overlap:
    :param norm:
    :param fps:
    :return:
    """
    def softmax(scores_row_vector):
        """
        the function takes the softmax of row numpy vector
        input:
            scores_row_vector: a row vector of of type numpy float32
        resturn:
            softmax_scores: row vector of of type numpy float32
        """
        scores_torch = torch.from_numpy(scores_row_vector).float().cuda()
        scores_torch = torch.nn.Softmax(scores_torch) #across rows
        
        return scores_torch.data.cpu().numpy()

#    print("score")
#    print(score)
#    print("tupe of score", type(score))
#    print("score size: ", score.shape)
#    print('---------------------------------')
    
    frm_max = score.max(axis=1)
#    print("frm_max.")
#    print(frm_max)
#    print("frm_max size: ", frm_max.shape)
#    print('---------------------------------')
    slide_score = []

    def top_k_pool(scores, k):
        return np.sort(scores, axis=0)[-k:, :].mean(axis=0)

    for t_span in spans:
        span = t_span * fps
        step = int(np.ceil(span * (1-overlap)))
        local_agg = [frm_max[i: i+span].max(axis=0) for i in range(0, frm_max.shape[0], step)]
        k = max(15, len(local_agg)/4)
        slide_score.append(top_k_pool(np.array(local_agg), k))

    out_score = np.mean(slide_score, axis=0)

    if norm:
        return softmax(out_score)
    else:
        return out_score

"""
----------------------------------------------------------------------------
"""



#this function takes one video at a time and outputs the first 5 scores
def one_video():

  #this function do forward propagation and returns scores
  def eval_video(data):
      """
      Evaluate single video
      video_data : Tuple has 3 elments (data in shape (crop_number,num_segments*length,H,W), label)
      return     : predictions and labels
      """
      if args.modality == 'RGB':
          length = 3
      elif args.modality == 'RGBDiff':
          length = 18
      else:
          raise ValueError("Unknown modality " + args.modality)
    
      with torch.no_grad():
          #reshape data to be in shape of (num_segments*crop_number,length,H,W)
          input = data.view(-1, length, data.size(1), data.size(2))
          #Forword Propagation
          output = model(input)
          #output_np = output.data.cpu().numpy().copy()
          #Reshape numpy array to (num_crop,num_segments,num_classes)
          output_torch = output.view((num_crop, test_segments, num_class))
         
          #Take mean of cropped images to be in shape (num_segments,1,num_classes)
          output_torch = output_torch.mean(dim=0).view((test_segments,1,num_class))
#          print("output np:")
#          print(output_torch)
#          print("type", type(output_torch))
#          print("size of output np: ", output_torch.shape)
          #output_np = output_np.mean(axis=0)
      return output_torch     
  
  #this function used to pick 25 frames only from the whole video
  def frames_indices(frames):
      FPSeg = len(frames) // test_segments
      offset = [x*FPSeg for x in range(test_segments)]
      random_indices = list(randint(FPSeg,size=test_segments))
      frame_indices = [sum(i) for i in zip(random_indices,offset)]
      return frame_indices        
    
  num_crop = args.test_crops  
  test_segments = args.test_segments
  

  """
  --------------------Inzializations---------------------------
  """
    
  action_label = label_dic(args.classInd_file)

  if args.dataset == 'ucf101':
      num_class = 101
  else:
      raise ValueError('Unkown dataset: ' + args.dataset)
  
  model = TSN_model(num_class, 1, args.modality,
                    base_model_name=args.arch, consensus_type='avg', dropout=args.dropout)
  
  #load the weights of your model training
  checkpoint = torch.load(args.weights)
  print("epoch {}, best acc1@: {}" .format(checkpoint['epoch'], checkpoint['best_acc1']))

  base_dict = {'.'.join(k.split('.')[1:]): v for k,v in list(checkpoint['state_dict'].items())}
  model.load_state_dict(base_dict)
  
  #test_crops is set to 1 for fast video evaluation
  if args.test_crops == 1:
      cropping = torchvision.transforms.Compose([
          GroupScale(model.scale_size),
          GroupCenterCrop(model.input_size),
      ])
  elif args.test_crops == 10:
      cropping = torchvision.transforms.Compose([
          GroupOverSample(model.input_size, model.scale_size)
      ])
  else:
      raise ValueError("Only 1 and 10 crops are supported while we got {}".format(test_crops))
      
  #Required transformations
  transform = torchvision.transforms.Compose([
           cropping,
           Stack(roll=args.arch == 'BNInception'),
           ToTorchFormatTensor(div=args.arch != 'BNInception'),
           GroupNormalize(model.input_mean, model.input_std),
                   ])
    
    
  if args.gpus is not None:
      devices = [args.gpus[i] for i in range(args.workers)]
  else:
      devices = list(range(args.workers))
    
  model = torch.nn.DataParallel(model.cuda(devices[0]), device_ids=devices)
         
  model.eval()    

  softmax = torch.nn.Softmax() #across rows
   
  
  """
  --------------Captureing the frames from  the video-------------------
  """
  
  frames = []  
  capture = cv2.VideoCapture(args.video)
  frame_count = 0
  
  while True:
      ret, orig_frame = capture.read()
     
      if ret is True:
          frame = cv2.cvtColor(orig_frame, cv2.COLOR_BGR2RGB)
      else:
          break
      
      #RGB_frame is used for plotting a frame with text of top-5 scores on it
      RGB_frame = frame    
        
      #use .fromarray function to be able to apply different data augmentations
      frame = Image.fromarray(frame)
      
      frame_count += 1
      frames.append(frame) 
  
  print(frame_count)
  
  # When everything done, release the capture
  capture.release()
  #cv2.destroyAllWindows()    
  
  #to evaluate the processing time
  start_time = time.time()
  
  """
  ----------------------------------------------------------------------
  """
  '''
  images = [cv2.imread(file) for file in glob.glob(args.video + "/*.jpg")]
  
  for frame in images:    
      frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
      frame = Image.fromarray(frame)
      frames.append(frame)   
  '''

  
  indices = frames_indices(frames)
  num_segments = len(indices)
  
  frames_a = operator.itemgetter(*indices)(frames)
  frames_a = transform(frames_a).cuda()
  
  scores = torch.zeros((num_segments, 1, 101), dtype=torch.float32).cuda()
  scores = eval_video(frames_a)

  #scores = softmax(scores)
  scores = scores.data.cpu().numpy().copy() #now we got the scores of each segment
  
  
#  print("scores")
#  print(scores)
#  print("tupe of scores", type(scores))
#  print("score size: ", scores.shape)
#  print('---------------------------------')
  
  
  out_scores = np.zeros((num_segments, 1, 101), dtype=float)
  out_scores = sliding_window_aggregation_func(scores, spans=[1], norm=True)
  
  print("output scores of the segments.")
  print(out_scores)
  print('---------------------------------')
  print("scores size: ", out_scores.shape)
  
  
  end_time = time.time() - start_time
  print("time taken: ", end_time)
  
  
  """
  ---------------Display the resulting frame and the classified action---------
  """
#  font = cv2.FONT_HERSHEY_SIMPLEX
#  y0, dy = 300, 40
#  k=0
#  print('Top 5 actions: ')
#  #get the top-5 classified actions
#  for i in np.argsort(scores)[0][::-1][:5]:
#      print('%-22s %0.2f%%' % (action_label[str(i+1)], scores[0][i] * 100))
#      #this equation is used to print different actions on a separate line
#      y = y0 + k * dy
#      k+=1
#      cv2.putText(RGB_frame, text='{} - {:.2f}'.format(action_label[str(i+1)],scores[0][i]), 
#                       org=(5,y),fontFace=font, fontScale=1,
#                       color=(0,0,255), thickness=2)
#  
#  #save the frame in your current working directory
#  cv2.imwrite(current_dir + 'text_frame'+'.png', RGB_frame)
#  #plt.imshow(img)
#  #plt.show()


  
if __name__ == '__main__':
    one_video()