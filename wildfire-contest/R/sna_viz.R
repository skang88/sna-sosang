# 강석균 파트: 산청·하동 산불 소상공인 지원 네트워크 시각화 (R)
# 입력: recon.json (인포그래픽 재구성 네트워크, 보고 지표와 일치 검증됨)
# 최종본은 원자료 '산청하동_경남도민·경남일보_마이크로데이터.xlsx'로 재실행할 것
suppressPackageStartupMessages({library(jsonlite);library(igraph);library(ggraph);library(ggplot2)
  library(patchwork);library(dplyr);library(tidyr);library(scales)})
FONT <- "NanumGothic"; theme_set(theme_minimal(base_family=FONT, base_size=11))
args <- commandArgs(trailingOnly=TRUE); inp <- ifelse(length(args)>0,args[1],"recon.json")
d <- fromJSON(inp, simplifyVector=FALSE); P <- names(d)
pal <- c("현지"="#D9480F","경남"="#1C6DD0","전국"="#A7AEB7")
mk <- function(p){ nd <- bind_rows(lapply(d[[p]]$nodes, function(x) data.frame(name=x$id, scale=factor(x$scale,levels=c("현지","경남","전국")),
        label=ifelse(is.null(x$label),"",x$label))))
  ed <- bind_rows(lapply(d[[p]]$edges, function(x) data.frame(from=x$from,to=x$to,weight=x$weight)))
  graph_from_data_frame(ed, directed=FALSE, vertices=nd) }
G <- setNames(lapply(P, mk), P)
cent_deg <- function(g){n<-vcount(g);k<-degree(g);sum(max(k)-k)/((n-1)*(n-2))}
cent_btw <- function(g){n<-vcount(g);b<-betweenness(g,weights=NA,normalized=TRUE);sum(max(b)-b)/(n-1)}
M <- bind_rows(lapply(P,function(p){g<-G[[p]];data.frame(period=p,nodes=vcount(g),ties=ecount(g),
  density=edge_density(g),degC=cent_deg(g),btwC=cent_btw(g))}))
M$period <- factor(M$period, levels=P); print(M, digits=2); write.csv(M,"fig/metrics.csv",row.names=FALSE)

# 그림 1. 시기별 네트워크
set.seed(42)
plots <- lapply(P, function(p){ g <- G[[p]]; V(g)$btw <- betweenness(g, weights=NA, normalized=TRUE)
  ggraph(g, layout="stress") + geom_edge_link(aes(width=weight), colour="grey70", alpha=.6, show.legend=FALSE) +
   scale_edge_width(range=c(.3,1.6)) + geom_node_point(aes(fill=scale, size=btw), shape=21, colour="white", stroke=.4) +
   geom_node_text(aes(label=label), family=FONT, size=2.8, repel=TRUE, max.overlaps=20) +
   scale_fill_manual(values=pal, name="조직 규모", drop=FALSE) + scale_size(range=c(2,10), guide="none") +
   labs(title=p, subtitle=sprintf("조직 %d · 관계 %d · 밀도 %.2f", vcount(g), ecount(g), edge_density(g))) +
   theme_graph(base_family=FONT) + theme(plot.title=element_text(face="bold",size=13)) })
f1 <- wrap_plots(plots, ncol=2, guides="collect") & theme(legend.position="bottom")
ggsave("fig/fig1_networks.png", f1, width=10, height=9, dpi=300, bg="white")

# 그림 2. 중앙화 추이 vs 오션포트(Harris & Doerfel, 2017)
oc <- data.frame(stage=c("대응기","단기복구","장기복구"), value=c(.61,.59,.78), case="오션포트(2017)")
ours <- data.frame(stage=c("대응기","단기복구","장기복구"), value=M$degC[2:4], case="산청·하동(본 연구)")
cc <- rbind(oc,ours); cc$stage <- factor(cc$stage, levels=c("대응기","단기복구","장기복구"))
f2 <- ggplot(cc, aes(stage, value, colour=case, group=case)) + geom_line(linewidth=1.1) + geom_point(size=3) +
  geom_text(aes(label=percent(value,1)), vjust=-1, family=FONT, size=3.5, show.legend=FALSE) +
  scale_y_continuous(labels=percent, limits=c(0,1)) + scale_colour_manual(values=c("#495057","#D9480F")) +
  labs(x=NULL, y="연결 중앙화", colour=NULL, caption="주: 본 연구 T0(평시) 연결 중앙화 20%는 비교 대상 없음") +
  theme(legend.position="top")
ggsave("fig/fig2_centralization_vs_oceanport.png", f2, width=7, height=4.5, dpi=300, bg="white")

# 그림 3. 구조 지표 4종
ml <- M |> pivot_longer(c(nodes,density,degC,btwC)) |>
  mutate(name=factor(name, levels=c("nodes","density","degC","btwC"), labels=c("조직 수","밀도","연결 중앙화","매개 중앙화")))
f3 <- ggplot(ml, aes(period, value, group=1)) + geom_line(colour="#1C6DD0") + geom_point(size=2.5, colour="#1C6DD0") +
  geom_text(aes(label=ifelse(value>1, value, sprintf("%.2f",value))), vjust=-.9, family=FONT, size=3) +
  facet_wrap(~name, scales="free_y", nrow=1) + scale_y_continuous(expand=expansion(c(.05,.25))) + labs(x=NULL,y=NULL)
ggsave("fig/fig3_structure.png", f3, width=10, height=3.6, dpi=300, bg="white")

# 그림 4. 상위 중개자(정규화 매개중심성)
br <- bind_rows(lapply(P[-1], function(p){g<-G[[p]]; b<-betweenness(g,weights=NA,normalized=TRUE)
  data.frame(period=p, org=ifelse(V(g)$label=="", V(g)$name, V(g)$label), scale=V(g)$scale, btw=b) |>
  arrange(desc(btw)) |> head(3)}))
br$period <- factor(br$period, levels=P[-1]); print(br, digits=2)
f4 <- ggplot(br, aes(reorder(paste(org,period,sep="\n"), btw), btw, fill=scale)) + geom_col(width=.65) +
  geom_text(aes(label=sprintf("%.2f",btw)), hjust=-.2, family=FONT, size=3.3) + coord_flip() +
  facet_wrap(~period, scales="free_y", ncol=1) + scale_fill_manual(values=pal, name="규모") +
  scale_y_continuous(limits=c(0,.6)) + labs(x=NULL, y="매개중심성(정규화)") +
  theme(strip.text=element_text(face="bold"))
ggsave("fig/fig4_brokers.png", f4, width=6.5, height=6.5, dpi=300, bg="white")

# 그림 5. 조직 규모 구성
sc <- bind_rows(lapply(P, function(p) data.frame(period=p, scale=V(G[[p]])$scale))) |> count(period, scale) |>
  group_by(period) |> mutate(share=n/sum(n)) |> ungroup() |> mutate(period=factor(period,levels=P), scale=factor(scale,levels=c("전국","경남","현지")))
print(sc)
f5 <- ggplot(sc, aes(period, share, fill=scale)) + geom_col(width=.6) +
  geom_text(aes(label=percent(share,1)), position=position_stack(vjust=.5), colour="white", family=FONT, size=3.4) +
  scale_fill_manual(values=pal, name="규모") + scale_y_continuous(labels=percent) + labs(x=NULL, y="조직 비중")
ggsave("fig/fig5_scale_composition.png", f5, width=6.5, height=4.2, dpi=300, bg="white")

# 그림 6. 시기별 의제 구성(보고값)
tp <- data.frame(period=rep(P,c(2,3,5,3)),
  topic=c("전통시장","기타", "산불 금융","공제","기타", "상품권","산불 금융","호우","전통시장","기타", "전통시장","상품권","기타·복합"),
  share=c(.94,.06, .64,.26,.10, .26,.25,.24,.21,.04, .32,.23,.45))
tp$period <- factor(tp$period, levels=P)
tcol <- c("전통시장"="#2B8A3E","산불 금융"="#D9480F","공제"="#F08C00","상품권"="#1C6DD0","호우"="#5F3DC4","기타"="#CED4DA","기타·복합"="#CED4DA")
f6 <- ggplot(tp, aes(period, share, fill=topic)) + geom_col(width=.6) +
  geom_text(aes(label=ifelse(share>=.08, percent(share,1), "")), position=position_stack(vjust=.5), colour="white", family=FONT, size=3.2) +
  scale_fill_manual(values=tcol, name="의제") + scale_y_continuous(labels=percent) + labs(x=NULL, y="기사 내 의제 비중")
ggsave("fig/fig6_topics.png", f6, width=6.5, height=4.2, dpi=300, bg="white")

# 그림 7. 관계 지속·신규(보고값)
ps <- data.frame(trans=factor(rep(c("T1·T2 → T3","T3 → T4"),each=2),levels=c("T1·T2 → T3","T3 → T4")),
  type=factor(rep(c("지속 관계","신규 관계"),2),levels=c("신규 관계","지속 관계")), n=c(5,41,5,86))
f7 <- ggplot(ps, aes(trans, n, fill=type)) + geom_col(width=.55) +
  geom_text(aes(label=n), position=position_stack(vjust=.5), colour="white", family=FONT, size=3.8) +
  scale_fill_manual(values=c("지속 관계"="#D9480F","신규 관계"="#ADB5BD"), name=NULL) +
  labs(x=NULL, y="다음 시기 관계 수", caption="주: 대응기 관계 67개 중 단기복구기로 이어진 관계는 5개") + theme(legend.position="top")
ggsave("fig/fig7_tie_persistence.png", f7, width=6, height=4.2, dpi=300, bg="white")

# 보조: Gould-Fernandez 중개 역할(규모를 집단으로)
library(sna, warn.conflicts=FALSE, quietly=TRUE)
gf <- bind_rows(lapply(P[-1], function(p){g<-G[[p]]; A<-as.matrix(as_adjacency_matrix(g,sparse=FALSE)); A[A>0]<-1
  r <- sna::brokerage(A, V(g)$scale)$raw.nli; data.frame(period=p, org=ifelse(V(g)$label=="",V(g)$name,V(g)$label), r) |>
  arrange(desc(t)) |> head(3)}))
print(gf); write.csv(gf,"fig/gould_fernandez_top3.csv",row.names=FALSE)
