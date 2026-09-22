import { createRouter, createWebHistory } from 'vue-router'

const stages = new Set(['script', 'shots', 'storyboards', 'clips', 'final-cut'])

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/',
      name: 'studio',
      component: () => import('../views/StudioView.vue'),
    },
    {
      path: '/projects/:projectId/:stage/:shotId?',
      name: 'project-stage',
      component: () => import('../views/StudioView.vue'),
      beforeEnter: (to) => {
        const stage = to.params.stage
        if (typeof stage !== 'string') {
          return { name: 'project-stage', params: { projectId: to.params.projectId, stage: 'script' } }
        }
        // Storyboards + clips collapse into Produce (shots)
        if (stage === 'storyboards' || stage === 'clips') {
          return {
            name: 'project-stage',
            params: {
              projectId: to.params.projectId,
              stage: 'shots',
              ...(typeof to.params.shotId === 'string' ? { shotId: to.params.shotId } : {}),
            },
          }
        }
        if (stages.has(stage)) return true
        return {
          name: 'project-stage',
          params: { projectId: to.params.projectId, stage: 'script' },
        }
      },
    },
    {
      path: '/:pathMatch(.*)*',
      redirect: '/',
    },
  ],
})

export default router
