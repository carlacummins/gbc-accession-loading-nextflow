FROM continuumio/miniconda3

ADD environment.yaml /tmp/environment.yaml
RUN conda env create -f /tmp/environment.yaml

RUN echo "conda activate gbc-conda" >> ~/.bashrc
ENV PATH=/opt/conda/envs/gbc-conda/bin:$PATH

# download python module extra requirements
RUN python -m spacy download en_core_web_sm
RUN python -m nltk.downloader -d /usr/local/share/nltk_data punkt_tab
RUN python -m nltk.downloader -d /usr/local/share/nltk_data averaged_perceptron_tagger_eng
RUN python -m nltk.downloader -d /usr/local/share/nltk_data maxent_ne_chunker_tab
RUN python -m nltk.downloader -d /usr/local/share/nltk_data words
ENV NLTK_DATA=/usr/local/share/nltk_data

ADD globalbiodata.py /opt/globalbiodata.py
ENV PYTHONPATH=/opt