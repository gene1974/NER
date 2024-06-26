import json
import numpy as np
import os
import pickle
import time
import torch
import torch.nn as nn
import torch.optim as optim

from BiLSTM_CRF import BiLSTM_CRF
from CCKSData import CCKSDataset, CCKSVocab
from LexiconEmbedding import LexiconVocab
from TagVocab import TagVocab
from pytorchtools import EarlyStopping
from Utils import logger, label_chinese_entity

torch.manual_seed(1)
os.environ["CUDA_VISIBLE_DEVICES"] = '0'

class Trainer():
    def __init__(self, 
        mod = 'train', model_time = None, data_path = None, epochs = 100, 
        use_word = True, use_char = True, use_lm = True, use_crf = True, use_cnn = True, use_lexicon = True, 
        use_pretrained_word = True, use_pretrained_char = True, 
        attention_pooling = False
        ):
        super().__init__()

        self.char_emb_dim = 256
        self.word_emb_dim = 256
        self.lm_emb_dim = 256
        self.lexicon_emb_dim = 256
        self.emb_dim = 256
        self.hidden_dim = 256
        self.lstm_layers = 1
        self.dropout = 0.1
        self.epochs = epochs
        self.batch_size = 8

        self.use_word = use_word
        self.use_char = use_char
        self.use_cnn = use_cnn
        self.use_crf = use_crf
        self.use_lm = use_lm
        self.use_lexicon = use_lexicon
        self.use_pretrained_word = use_pretrained_word
        self.use_pretrained_char = use_pretrained_char
        self.lr = 0.0001
        self.momentum = 0.9
        self.decay_rate = 0.05
        self.gradient_clip = 5.0
        self.device = 'cuda' if torch.cuda.is_available() else 'cpu'
        logger('device = {}'.format(self.device))
        logger('use_word = {}, use_char = {}, use_lm = {}, use_crf = {}, use_cnn = {}, use_lexicon = {}, atten_pool = {}'.format(use_word, use_char, use_lm, use_crf, use_cnn, use_lexicon, attention_pooling))
        logger('use_pretrained_word = {}, use_pretrained_char = {}'.format(use_pretrained_word, use_pretrained_char))
        logger('dataset_path = {}'.format(data_path))
        #logger('pretrained_path = {}'.format(pretrained_path))

        
        if mod == 'train':
            self.vocab = CCKSVocab(data_path)
            self.tag_vocab = self.vocab.tag_vocab
            if self.use_lexicon:
                self.lexicon_vocab = LexiconVocab(self.tag_vocab)
            self.train_set = CCKSDataset(data_path, self.vocab, self.tag_vocab, mod = 'train')
            self.valid_set = self.train_set.valid_set
            self.test_set = CCKSDataset(data_path, self.vocab, self.tag_vocab, mod = 'test')
            logger('Load data. Train data: {}, Valid data: {}, Test data: {}'.format(len(self.train_set), len(self.valid_set), len(self.test_set)))
        else:
            model_path = './results/{}'.format(model_time)
            with open(model_path + '/vocab_' + model_time, 'rb') as f:
                self.vocab = pickle.load(f)
                self.tag_vocab = self.vocab.tag_vocab
                if use_lexicon:
                    self.lexicon_vocab = pickle.load(f)
            self.test_set = CCKSDataset(data_path, self.vocab, self.tag_vocab, mod = 'test')
            logger('Load data. Test data: {}'.format(len(self.test_set)))

        self.model = BiLSTM_CRF(
            self.vocab, self.tag_vocab,
            self.char_emb_dim, self.word_emb_dim, self.lm_emb_dim, self.lexicon_emb_dim, self.emb_dim, self.hidden_dim, self.lstm_layers, 
            self.batch_size, self.device, self.dropout, 
            use_word = use_word, use_char = use_char, use_lm = use_lm, use_crf = use_crf, use_cnn = use_cnn, use_lexicon = use_lexicon,
            use_pretrained_word = use_pretrained_word, use_pretrained_char = use_pretrained_char, 
            attention_pooling = attention_pooling
        ).to(self.device)

        if mod == 'train':
            self.train()
        else:
            self.model.load_state_dict(torch.load(model_path + '/model_' + model_time))
            #self.test()
            self.test_with_lexicon()

    def train(self):
        model = self.model
        # optimizer = optim.SGD(model.parameters(), lr = self.lr, weight_decay = self.decay_rate, momentum = self.momentum)
        optimizer = optim.Adam(model.parameters(), lr = 1e-4)
        early_stopping = EarlyStopping(patience = 10, verbose = False)
        entrophy = nn.CrossEntropyLoss()

        avg_train_losses = []
        avg_valid_losses = []
        for epoch in range(self.epochs):
            train_losses = []
            valid_losses = []
            model.train()
            i = 0
            while i < len(self.train_set):
                if i + self.batch_size < len(self.train_set):
                    batch = self.train_set[i: i + self.batch_size]
                else:
                    batch = self.train_set[i:]
                i += self.batch_size
                text, char_ids, char_mask, tag_ids = batch

                sen_len = max([len(sentence) for sentence in text])
                char_ids = char_ids[:, : sen_len].to(self.device)
                tag_ids = tag_ids[:, : sen_len].to(self.device)
                char_mask = char_mask[:, : sen_len].to(self.device)
                
                optimizer.zero_grad()
                if self.use_crf:
                    loss = model(text, None, None, char_ids, char_mask, tag_ids) # (batch_size, sen_len, tagset_size)
                else:
                    output = model(text, None, None, char_ids, char_mask) # (batch_size, sen_len, tagset_size)
                    output = output.permute(0, 2, 1) # (batch_size, tagset_size, sen_len)
                    loss = entrophy(output, tag_ids)
                train_losses.append(loss.item())
                loss.backward()
                optimizer.step()
            
            model.eval()
            with torch.no_grad():
                i = 0
                while i < len(self.valid_set):
                    if i + self.batch_size < len(self.valid_set):
                        batch = self.valid_set[i: i + self.batch_size]
                    else:
                        batch = self.valid_set[i:]
                    i += self.batch_size
                    text, char_ids, char_mask, tag_ids = batch

                    sen_len = max([len(sentence) for sentence in text])
                    char_ids = char_ids[:, : sen_len].to(self.device)
                    tag_ids = tag_ids[:, : sen_len].to(self.device)
                    char_mask = char_mask[:, : sen_len].to(self.device)

                    if self.use_crf:
                        loss = model(text, None, None, char_ids, char_mask, tag_ids) # (batch_size, sen_len, tagset_size)
                    else:
                        output = model(text, None, None, char_ids, char_mask) # (batch_size, sen_len, tagset_size)
                        output = output.permute(0, 2, 1) # (batch_size, tagset_size, sen_len)
                        loss = entrophy(output, tag_ids)
                    valid_losses.append(loss.item())
                avg_train_loss = np.average(train_losses)
                avg_valid_loss = np.average(valid_losses)
                avg_train_losses.append(avg_train_loss)
                avg_valid_losses.append(avg_valid_loss)
                logger('[epoch {:3d}] train_loss: {:.8f}  valid_loss: {:.8f}'.format(epoch + 1, avg_train_loss, avg_valid_loss))
                early_stopping(avg_valid_loss, model)
                if early_stopping.early_stop:
                    logger("Early stopping")
                    break

        self.model = model
        model_time = '{}'.format(time.strftime('%m%d%H%M', time.localtime()))
        model_path = './results/{}'.format(model_time)
        os.mkdir(model_path)
        torch.save(model.state_dict(), model_path + '/model_' + model_time)
        with open(model_path + '/vocab_' + model_time, 'wb') as f:
            pickle.dump(self.vocab, f)
            if self.use_lexicon:
                pickle.dump(self.model.lexicon_embeds.lexicon_vocab)

        logger('Save result {}'.format(model_time))

        self.test()
            
    def test(self):
        model = self.model
        model.eval()
        gold_num, predict_num, correct_num = 0, 0, 0
        relax_correct_num = 0
        correct = 0
        total = 0
        logger('Begin testing.')
        with torch.no_grad():
            i = 0
            while i < len(self.test_set):
                if i + self.batch_size < len(self.test_set):
                    batch = self.test_set[i: i + self.batch_size]
                else:
                    batch = self.test_set[i:]
                i += self.batch_size
                text, char_ids, char_mask, tag_ids = batch

                sen_len = max([len(sentence) for sentence in text])
                char_ids = char_ids[:, : sen_len].to(self.device)
                tag_ids = tag_ids[:, : sen_len].to(self.device)
                char_mask = char_mask[:, : sen_len].to(self.device)
                
                if self.use_crf:
                    predict = model(text, None, None, char_ids, char_mask) # (batch_size, sen_len)
                else:
                    output = model(text, None, None, char_ids, char_mask) # (batch_size, sen_len, tagset_size)
                    predict = torch.max(output, dim = 2).indices # (batch_size, sen_len)
                correct += torch.sum(predict[char_mask] == tag_ids[char_mask]).item()
                total += torch.sum(char_mask).item()
                
                for j in range(tag_ids.shape[0]):
                    gold_entity = label_chinese_entity(text[j], tag_ids[j].tolist(), self.tag_vocab.ix_to_tag)
                    pred_entity = label_chinese_entity(text[j], predict[j], self.tag_vocab.ix_to_tag)
                    gold_num += len(gold_entity)
                    predict_num += len(pred_entity)
                    correct_entity = []
                    relax_correct_entity = []
                    relax_correct_entity_gold = []
                    relax_correct_entity_pred = []
                    # print('correct:')
                    # for entity in gold_entity:
                    #     if entity in pred_entity:
                    #         correct_entity.append(entity)
                    #         correct_num += 1
                    #         print(entity)
                    for gold in gold_entity:
                        for pred in pred_entity:
                            # [start, end)
                            if max(pred['start_pos'], gold['start_pos']) < min(pred['end_pos'], gold['end_pos']) and pred['label'] == gold['label']:
                                relax_correct_num += 1
                                if gold == pred:
                                    correct_num += 1
                                    correct_entity.append(gold)
                                else:
                                    relax_correct_entity.append([gold, pred])
                                    relax_correct_entity_gold.append(gold)
                                    relax_correct_entity_pred.append(pred)
                    # print ner results
                    print(''.join(text[j]))
                    print('correct:')
                    for e in correct_entity:
                        print(e)
                    print('relax correct:')
                    for e in relax_correct_entity:
                        print(e[0], e[1])
                    print('gold:')
                    for e in gold_entity:
                        if e not in correct_entity and e not in relax_correct_entity_gold:
                            print(e)
                    print('predict:')
                    for e in pred_entity:
                        if e not in correct_entity and e not in relax_correct_entity_pred:
                            print(e)
                    print()

            precision = correct_num / (predict_num + 0.000000001)
            recall = correct_num / (gold_num + 0.000000001)
            f1 = 2 * precision * recall / (precision + recall + 0.000000001)
            logger('[Test] Tagging accuracy: {:.8f}'.format(correct / total))
            logger('[Test] Precisely matching:')
            logger('[Test] Precision: {:.8f} Recall: {:.8f} F1: {:.8f}'.format(precision, recall, f1))
            precision = relax_correct_num / (predict_num + 0.000000001)
            recall = relax_correct_num / (gold_num + 0.000000001)
            f1 = 2 * precision * recall / (precision + recall + 0.000000001)
            logger('[Test] Relaxation matching:')
            logger('[Test] Precision: {:.8f} Recall: {:.8f} F1: {:.8f}'.format(precision, recall, f1))
    
    def test_with_lexicon(self, use_rule = False):
        model = self.model
        model.eval()
        gold_num, predict_num, correct_num = 0, 0, 0
        relax_correct_num = 0
        correct = 0
        total = 0
        if not self.use_lexicon:
            self.lexicon_vocab = LexiconVocab(self.tag_vocab)
        logger('Begin testing.')
        with torch.no_grad():
            i = 0
            while i < len(self.test_set):
                if i + self.batch_size < len(self.test_set):
                    batch = self.test_set[i: i + self.batch_size]
                else:
                    batch = self.test_set[i:]
                i += self.batch_size
                text, char_ids, char_mask, tag_ids = batch

                sen_len = max([len(sentence) for sentence in text])
                char_ids = char_ids[:, : sen_len].to(self.device)
                tag_ids = tag_ids[:, : sen_len].to(self.device)
                char_mask = char_mask[:, : sen_len].to(self.device)
                
                if self.use_crf:
                    predict = model(text, None, None, char_ids, char_mask) # (batch_size, sen_len)
                else:
                    output = model(text, None, None, char_ids, char_mask) # (batch_size, sen_len, tagset_size)
                    predict = torch.max(output, dim = 2).indices # (batch_size, sen_len)
                correct += torch.sum(predict[char_mask] == tag_ids[char_mask]).item()
                total += torch.sum(char_mask).item()
                
                for j in range(tag_ids.shape[0]):
                    new_tag_id = self.lexicon_vocab.clean_tagid_with_lexicon(''.join(text[j]), tag_ids[j], use_rule)
                    print(new_tag_id)
                    1()
                    gold_entity = label_chinese_entity(text[j], tag_ids[j].tolist(), self.tag_vocab.ix_to_tag)
                    pred_entity = label_chinese_entity(text[j], predict[j], self.tag_vocab.ix_to_tag)
                    gold_num += len(gold_entity)
                    predict_num += len(pred_entity)
                    correct_entity = []
                    relax_correct_entity = []
                    relax_correct_entity_gold = []
                    relax_correct_entity_pred = []
                    for gold in gold_entity:
                        for pred in pred_entity:
                            # [start, end)
                            if max(pred['start_pos'], gold['start_pos']) < min(pred['end_pos'], gold['end_pos']) and pred['label'] == gold['label']:
                                relax_correct_num += 1
                                if gold == pred:
                                    correct_num += 1
                                    correct_entity.append(gold)
                                else:
                                    relax_correct_entity.append([gold, pred])
                                    relax_correct_entity_gold.append(gold)
                                    relax_correct_entity_pred.append(pred)
                    # print ner results
                    print(''.join(text[j]))
                    print('correct:')
                    for e in correct_entity:
                        print(e)
                    print('relax correct:')
                    for e in relax_correct_entity:
                        print(e[0], e[1])
                    print('gold:')
                    for e in gold_entity:
                        if e not in correct_entity and e not in relax_correct_entity_gold:
                            print(e)
                    print('predict:')
                    for e in pred_entity:
                        if e not in correct_entity and e not in relax_correct_entity_pred:
                            print(e)
                    print()

            precision = correct_num / (predict_num + 0.000000001)
            recall = correct_num / (gold_num + 0.000000001)
            f1 = 2 * precision * recall / (precision + recall + 0.000000001)
            logger('[Test] Tagging accuracy: {:.8f}'.format(correct / total))
            logger('[Test] Precisely matching:')
            logger('[Test] Precision: {:.8f} Recall: {:.8f} F1: {:.8f}'.format(precision, recall, f1))
            precision = relax_correct_num / (predict_num + 0.000000001)
            recall = relax_correct_num / (gold_num + 0.000000001)
            f1 = 2 * precision * recall / (precision + recall + 0.000000001)
            logger('[Test] Relaxation matching:')
            logger('[Test] Precision: {:.8f} Recall: {:.8f} F1: {:.8f}'.format(precision, recall, f1))
    


if __name__ == '__main__':
    data_path = './data_small/'
    data_path = '/data/CCKS2019/'

    trainer = Trainer('test', '01041630',
        data_path, epochs = 100, 
        use_word = False, use_char = True, use_lm = True, use_crf = True, use_lexicon = False, 
        use_pretrained_word = False, use_pretrained_char = False, 
        attention_pooling = False)
